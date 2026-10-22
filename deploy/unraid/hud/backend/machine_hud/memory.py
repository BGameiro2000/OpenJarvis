"""Hybrid memory search and the two hop graph.

Vault full text search (local, ``vault.py``) and Cognee chunk search (vector,
no model call) run in parallel and are merged with reciprocal rank fusion.
The graph comes from Cognee's dataset graph, cut to two hops around a note;
when Cognee is not there, the vault's own [[wikilink]] graph stands in and
the panel says so.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from typing import Any

import httpx

from .vault import VaultIndex, shape_for

RRF_K = 60
DROP_TYPES = {"DocumentChunk", "TextSummary", "EntityType", "NodeSet", "Timestamp"}
DOC_TYPES = {"TextDocument", "Document", "PdfDocument", "AudioDocument", "ImageDocument"}


def upload_name(rel: str) -> str:
    """The name cognee-ingest gives a vault file (see cognee-ingest)."""
    return rel.replace("/", "__")


def rrf(lists: dict[str, list[str]]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for keys in lists.values():
        for rank, key in enumerate(keys):
            scores[key] += 1.0 / (RRF_K + rank + 1)
    return scores


def walk_chunks(value: Any) -> list[dict[str, Any]]:
    """Find chunk payloads (dicts with text) anywhere in a Cognee response."""
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("text"), str) and (
            "document_name" in value or "chunk_index" in value or "is_part_of" in value
        ):
            out.append(value)
        else:
            for v in value.values():
                out.extend(walk_chunks(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(walk_chunks(v))
    return out


def chunk_document(c: dict[str, Any]) -> str:
    name = c.get("document_name") or ""
    if not name and isinstance(c.get("is_part_of"), dict):
        name = c["is_part_of"].get("name", "")
    return str(name)


class CogneeGraph:
    """Cognee calls used by the HUD: chunk search and the dataset graph."""

    def __init__(self, client: httpx.AsyncClient, dataset: str = "vault") -> None:
        self.client = client
        self.dataset = dataset
        self._graph: tuple[float, dict[str, Any]] | None = None
        self._dataset_id: str | None = None

    async def search(self, q: str, top_k: int = 20) -> list[dict[str, Any]]:
        r = await self.client.post(
            "/api/v1/search",
            json={"search_type": "CHUNKS", "query": q, "datasets": [self.dataset], "top_k": top_k},
            timeout=8.0,
        )
        r.raise_for_status()
        return walk_chunks(r.json())

    async def dataset_id(self) -> str:
        if self._dataset_id:
            return self._dataset_id
        r = await self.client.get("/api/v1/datasets", timeout=5.0)
        r.raise_for_status()
        for d in r.json():
            if d.get("name") == self.dataset:
                self._dataset_id = str(d["id"])
                return self._dataset_id
        raise LookupError(f"dataset {self.dataset!r} not found in Cognee (run cognee-ingest)")

    async def graph(self) -> dict[str, Any]:
        if self._graph and time.time() - self._graph[0] < 120:
            return self._graph[1]
        ds = await self.dataset_id()
        r = await self.client.get(f"/api/v1/datasets/{ds}/graph", timeout=20.0)
        r.raise_for_status()
        data = r.json()
        self._graph = (time.time(), data)
        return data


def fold_graph(data: dict[str, Any]) -> tuple[dict[str, dict], dict[str, set[str]], dict]:
    """Cognee graph to notes and entities: chunks become 'mentions' edges."""
    nodes = {str(n["id"]): n for n in data.get("nodes", [])}
    adj: dict[str, set[str]] = defaultdict(set)
    labels: dict[tuple[str, str], str] = {}
    for e in data.get("edges", []):
        a, b = str(e.get("source")), str(e.get("target"))
        if a in nodes and b in nodes:
            adj[a].add(b)
            adj[b].add(a)
            labels[(a, b)] = labels[(b, a)] = str(e.get("label", ""))
    entity_type: dict[str, str] = {}
    for nid, n in nodes.items():
        if n.get("type") == "EntityType":
            for m in adj[nid]:
                entity_type.setdefault(m, str(n.get("label", "")).lower())
    out_adj: dict[str, set[str]] = defaultdict(set)
    out_labels: dict[tuple[str, str], str] = {}
    for nid, n in nodes.items():
        t = n.get("type")
        if t == "DocumentChunk":
            docs = [m for m in adj[nid] if nodes[m].get("type") in DOC_TYPES]
            ents = [m for m in adj[nid] if nodes[m].get("type") not in DROP_TYPES | DOC_TYPES]
            for d in docs:
                for en in ents:
                    out_adj[d].add(en)
                    out_adj[en].add(d)
                    out_labels[(d, en)] = out_labels[(en, d)] = "mentions"
        elif t not in DROP_TYPES:
            for m in adj[nid]:
                if nodes[m].get("type") not in DROP_TYPES:
                    out_adj[nid].add(m)
                    out_labels[(nid, m)] = labels.get((nid, m), "")
    kept = {k: v for k, v in nodes.items() if v.get("type") not in DROP_TYPES}
    for k in kept:
        kept[k] = {**kept[k], "_etype": entity_type.get(k, "")}
    return kept, out_adj, out_labels


def entity_shape(etype: str, label: str) -> str:
    e = etype.lower()
    if any(w in e for w in ("decision",)):
        return "decision"
    if any(w in e for w in ("project", "system", "software", "product", "tool")):
        return "project"
    if any(w in e for w in ("event", "pattern", "date", "meeting")):
        return "event"
    if any(w in e for w in ("god", "deity")):
        return "god"
    return "thing"


class Memory:
    def __init__(self, vault: VaultIndex, cognee: CogneeGraph | None, demo: bool = False) -> None:
        self.vault = vault
        self.cognee = cognee
        self.demo = demo

    # -------------------------------------------------------------- search
    async def search(
        self,
        q: str,
        mode: str = "hybrid",
        types: set[str] | None = None,
        date_from: str = "",
        date_to: str = "",
        exclude: list[str] | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        sources: dict[str, Any] = {}
        # Both sides need the index: graph hits map back to vault paths through it.
        await asyncio.to_thread(self.vault.refresh)

        async def run_vault() -> list[dict]:
            t = time.perf_counter()
            hits = await asyncio.to_thread(
                self.vault.search, q, limit, types, date_from, date_to, exclude
            )
            sources["vault"] = {
                "ok": True,
                "ms": round((time.perf_counter() - t) * 1000),
                "hits": len(hits),
            }
            return hits

        async def run_graph() -> list[dict]:
            t = time.perf_counter()
            try:
                if self.demo:
                    chunks = await asyncio.to_thread(self._demo_chunks, q)
                    label = "demo"
                elif self.cognee is None:
                    raise RuntimeError("Cognee not configured")
                else:
                    chunks = await self.cognee.search(q, top_k=limit)
                    label = "cognee"
                sources["graph"] = {
                    "ok": True,
                    "ms": round((time.perf_counter() - t) * 1000),
                    "hits": len(chunks),
                    "via": label,
                }
                return chunks
            except (httpx.HTTPError, RuntimeError, ValueError, LookupError) as exc:
                sources["graph"] = {
                    "ok": False,
                    "ms": round((time.perf_counter() - t) * 1000),
                    "reason": _reason(exc),
                }
                return []

        tasks = []
        if mode in ("hybrid", "vault"):
            tasks.append(run_vault())
        if mode in ("hybrid", "graph"):
            tasks.append(run_graph())
        results = await asyncio.gather(*tasks)
        vault_hits = results[0] if mode in ("hybrid", "vault") else []
        chunks = results[-1] if mode in ("hybrid", "graph") else []

        merged: dict[str, dict[str, Any]] = {}
        order: dict[str, list[str]] = {"vault": [], "graph": []}
        for h in vault_hits:
            merged[h["path"]] = {**h, "tags": {"VAULT"}}
            order["vault"].append(h["path"])
        for c in chunks:
            doc = chunk_document(c)
            path = self.vault.by_name(doc) if doc else None
            key = path or f"graph:{doc or c.get('id', '')}"
            if key in order["graph"]:
                continue
            d = self.vault.docs.get(path) if path else None
            if d and not self.vault.allowed(d, types, date_from, date_to, exclude):
                continue
            order["graph"].append(key)
            if key in merged:
                merged[key]["tags"].add("GRAPH")
                merged[key]["chunk"] = _clip(c.get("text", ""))
            else:
                merged[key] = {
                    "path": path or "",
                    "title": d.title if d else (doc or "graph chunk"),
                    "type": d.type if d else "graph",
                    "date": d.date if d else "",
                    "snippet": "",
                    "chunk": _clip(c.get("text", "")),
                    "tags": {"GRAPH"},
                }
        scores = rrf(order)
        hits = []
        for key, h in merged.items():
            tags = h.pop("tags")
            h["tag"] = "BOTH" if len(tags) == 2 else next(iter(tags))
            h["rrf"] = round(scores.get(key, 0.0), 5)
            h["key"] = key
            hits.append(h)
        hits.sort(key=lambda h: (-h["rrf"], h["key"]))
        return {
            "query": q,
            "mode": mode,
            "ms": round((time.perf_counter() - t0) * 1000),
            "sources": sources,
            "hits": hits[:limit],
            "notes": len(self.vault.docs),
        }

    def _demo_chunks(self, q: str) -> list[dict[str, Any]]:
        """Demo stand-in for Cognee: paragraph overlap, a different ranking."""
        words = {w for w in re.findall(r"\w{3,}", q.lower())}
        if not words:
            return []
        scored = []
        with self.vault.lock:
            rows = self.vault.db.execute("select path, body from notes").fetchall()
        for path, body in rows:
            for para in re.split(r"\n\s*\n", body):
                pw = set(re.findall(r"\w{3,}", para.lower()))
                overlap = len(words & pw)
                if overlap:
                    scored.append((overlap / (len(pw) ** 0.5 + 1), path, para))
        scored.sort(key=lambda s: -s[0])
        out, seen = [], set()
        for score, path, para in scored:
            if path in seen:
                continue
            seen.add(path)
            out.append(
                {"text": para.strip(), "document_name": upload_name(path), "score": round(score, 3)}
            )
            if len(out) >= 12:
                break
        return out

    # --------------------------------------------------------------- graph
    async def graph(self, path: str = "", node: str = "", q: str = "") -> dict[str, Any]:
        t0 = time.perf_counter()
        center_path = self.vault.by_name(path) if path else None
        if not self.demo and self.cognee is not None:
            try:
                data = await self.cognee.graph()
                g = await asyncio.to_thread(self._cognee_subgraph, data, center_path, node, q)
                if g["nodes"]:
                    g.update(ok=True, via="cognee", ms=round((time.perf_counter() - t0) * 1000))
                    return g
                reason = "the note is not in the Cognee graph yet (run cognee-ingest)"
            except (httpx.HTTPError, LookupError, ValueError, KeyError) as exc:
                reason = _reason(exc)
        elif self.demo:
            reason = "demo: graph built from the demo vault's [[wikilinks]]"
        else:
            reason = "Cognee not configured"
        g = (
            self.vault.link_graph(center_path)
            if center_path
            else {"nodes": [], "edges": [], "center": None}
        )
        g.update(
            ok=self.demo,
            via="wikilinks",
            reason=reason,
            ms=round((time.perf_counter() - t0) * 1000),
        )
        return g

    def _cognee_subgraph(self, data, center_path, node_id, q, hops=2, cap=150) -> dict[str, Any]:
        nodes, adj, labels = fold_graph(data)
        center = None
        if node_id and node_id in nodes:
            center = node_id
        if center is None and center_path:
            want = {upload_name(center_path), upload_name(center_path).rsplit(".", 1)[0]}
            for nid, n in nodes.items():
                name = str(n.get("label") or (n.get("properties") or {}).get("name", ""))
                if n.get("type") in DOC_TYPES and (name in want or name.rsplit(".", 1)[0] in want):
                    center = nid
                    break
        if center is None and q:
            ql = q.lower()
            for nid, n in nodes.items():
                if str(n.get("label", "")).lower() == ql:
                    center = nid
                    break
        if center is None:
            return {"nodes": [], "edges": [], "center": None}
        seen = {center: 0}
        frontier = [center]
        for depth in range(1, hops + 1):
            nxt = []
            for n in frontier:
                for m in sorted(adj.get(n, ()), key=lambda x: str(nodes[x].get("label", ""))):
                    if m not in seen and len(seen) < cap:
                        seen[m] = depth
                        nxt.append(m)
            frontier = nxt
        out_nodes = []
        for nid, depth in seen.items():
            n = nodes[nid]
            label = str(n.get("label") or (n.get("properties") or {}).get("name", "") or nid[:8])
            if n.get("type") in DOC_TYPES:
                p = self.vault.by_name(label)
                d = self.vault.docs.get(p) if p else None
                out_nodes.append(
                    {
                        "id": nid,
                        "label": d.title if d else label.replace("__", "/"),
                        "kind": shape_for(d.type) if d else "note",
                        "type": d.type if d else "document",
                        "path": p or "",
                        "depth": depth,
                        "date": d.date if d else "",
                    }
                )
            else:
                et = n.get("_etype", "")
                out_nodes.append(
                    {
                        "id": nid,
                        "label": label,
                        "kind": entity_shape(et, label),
                        "type": et or str(n.get("type", "")),
                        "path": "",
                        "depth": depth,
                        "date": "",
                        "description": str((n.get("properties") or {}).get("description", ""))[
                            :300
                        ],
                    }
                )
        edges = []
        for a in seen:
            for b in adj.get(a, ()):
                if b in seen and a < b:
                    edges.append({"source": a, "target": b, "label": labels.get((a, b), "")})
        return {"nodes": out_nodes, "edges": edges, "center": center}


def _clip(text: str, n: int = 280) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return f"Cognee refused the key ({code}); check COGNEE_API_KEY"
        return f"Cognee answered {code}"
    if isinstance(exc, httpx.ConnectError):
        return "Cognee is not reachable (container stopped?)"
    if isinstance(exc, httpx.TimeoutException):
        return "Cognee timed out"
    return str(exc) or type(exc).__name__
