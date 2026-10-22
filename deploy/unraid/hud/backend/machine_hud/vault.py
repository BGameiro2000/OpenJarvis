"""Read-only vault index: full text search, notes, wikilinks.

An in-memory SQLite FTS5 table over the vault's ``.md`` and ``.txt`` files,
rebuilt for changed files by modification time. Nothing is written to disk,
so it works on the read-only mount and the read-only container root.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKIP_DIRS = {"_backups", "archive", ".obsidian", ".trash", ".intake", "node_modules"}
EXTS = {".md", ".markdown", ".txt"}
MAX_BYTES = 1_000_000  # bigger files are indexed by their first megabyte
NOTE_CAP = 512_000
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
FOLDER_TYPE = {
    "projects": "project",
    "potential_projects": "project",
    "ideas": "idea",
    "gtd": "action",
    "reference": "reference",
    "wiki": "wiki",
    "daily": "note",
    "council": "council",
    "inbox": "inbox",
    "reports": "report",
    "decisions": "decision",
}
HISTORY_HEAD = re.compile(r"^#{1,6}\s*(history|log|changelog|timeline)\b", re.I | re.M)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Simple ``key: value`` frontmatter. Lists stay as their raw text."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip("\"'")
    body = text[end + 4 :].lstrip("\n")
    return meta, body


def note_type(rel: str, meta: dict[str, str]) -> str:
    t = meta.get("type", "").lower()
    if t:
        return t
    top = rel.split("/", 1)[0]
    return FOLDER_TYPE.get(top, "note")


def note_date(meta: dict[str, str], rel: str, mtime: float) -> str:
    for k in ("date", "updated", "created", "decided"):
        m = DATE.search(meta.get(k, ""))
        if m:
            return m.group(1)
    m = DATE.search(rel)
    if m:
        return m.group(1)
    return time.strftime("%Y-%m-%d", time.localtime(mtime))


def fts_query(q: str) -> str:
    """User text to a safe FTS5 query: every word as a quoted prefix term."""
    words = re.findall(r"[\w][\w'-]*", q, re.UNICODE)
    return " ".join(f'"{w.replace(chr(34), "")}"*' for w in words[:12])


@dataclass
class Doc:
    path: str
    title: str
    type: str
    date: str
    mtime: float
    links: list[str] = field(default_factory=list)


class VaultIndex:
    def __init__(self, root: Path, refresh_s: float = 60.0) -> None:
        self.root = root
        self.refresh_s = refresh_s
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute(
            "create virtual table notes using fts5(path unindexed, title, body, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        self.docs: dict[str, Doc] = {}
        self.lock = threading.Lock()
        self.last_scan = 0.0
        self.scan_ms = 0

    # ------------------------------------------------------------ indexing
    def _files(self) -> dict[str, tuple[Path, float]]:
        out: dict[str, tuple[Path, float]] = {}
        if not self.root.is_dir():
            return out
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
            )
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() not in EXTS or f.startswith("."):
                    continue
                try:
                    out[p.relative_to(self.root).as_posix()] = (p, p.stat().st_mtime)
                except OSError:
                    continue
        return out

    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self.last_scan < self.refresh_s:
            return
        t0 = time.perf_counter()
        files = self._files()
        with self.lock:
            gone = set(self.docs) - set(files)
            for rel in gone:
                self.db.execute("delete from notes where path = ?", (rel,))
                self.docs.pop(rel, None)
            for rel, (p, mtime) in files.items():
                old = self.docs.get(rel)
                if old and old.mtime == mtime:
                    continue
                try:
                    with p.open("rb") as fh:
                        text = fh.read(MAX_BYTES).decode("utf-8", errors="replace")
                except OSError:
                    continue
                meta, body = parse_frontmatter(text)
                title = meta.get("title") or _first_heading(body) or Path(rel).stem
                self.db.execute("delete from notes where path = ?", (rel,))
                self.db.execute(
                    "insert into notes(path, title, body) values (?, ?, ?)", (rel, title, body)
                )
                self.docs[rel] = Doc(
                    path=rel,
                    title=title,
                    type=note_type(rel, meta),
                    date=note_date(meta, rel, mtime),
                    mtime=mtime,
                    links=sorted({m.strip() for m in WIKILINK.findall(body)}),
                )
            self.db.commit()
        self.last_scan = time.time()
        self.scan_ms = round((time.perf_counter() - t0) * 1000)

    # -------------------------------------------------------------- search
    def search(
        self,
        q: str,
        limit: int = 30,
        types: set[str] | None = None,
        date_from: str = "",
        date_to: str = "",
        exclude: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.refresh()
        match = fts_query(q)
        if not match:
            return []
        with self.lock:
            rows = self.db.execute(
                "select path, snippet(notes, 2, '\u0001', '\u0002', ' … ', 24), bm25(notes, 0, 4.0, 1.0) "
                "from notes where notes match ? order by bm25(notes, 0, 4.0, 1.0) limit ?",
                (match, limit * 3),
            ).fetchall()
        out = []
        for path, snip, score in rows:
            d = self.docs.get(path)
            if not d or not self.allowed(d, types, date_from, date_to, exclude):
                continue
            out.append(
                {
                    "path": path,
                    "title": d.title,
                    "type": d.type,
                    "date": d.date,
                    "snippet": snip,
                    "score": round(-score, 3),
                }
            )
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def allowed(d: Doc, types, date_from: str, date_to: str, exclude) -> bool:
        if types and d.type not in types:
            return False
        if date_from and d.date < date_from:
            return False
        if date_to and d.date > date_to:
            return False
        for ex in exclude or []:
            ex = ex.strip("/")
            if ex and (d.path == ex or d.path.startswith(ex + "/")):
                return False
        return True

    # ---------------------------------------------------------------- notes
    def resolve(self, rel: str) -> Path | None:
        """A vault path from the browser, checked to stay inside the vault."""
        if not rel or "\x00" in rel:
            return None
        root = self.root.resolve()
        p = (root / rel).resolve()
        if root not in p.parents or p.suffix.lower() not in EXTS or not p.is_file():
            return None
        return p

    def note(self, rel: str) -> dict[str, Any] | None:
        self.refresh()
        p = self.resolve(rel)
        if p is None:
            return None
        rel = p.relative_to(self.root.resolve()).as_posix()
        with p.open("rb") as fh:
            raw = fh.read(NOTE_CAP + 1)
        text = raw[:NOTE_CAP].decode("utf-8", errors="replace")
        meta, body = parse_frontmatter(text)
        m = HISTORY_HEAD.search(body)
        current, history = (body[: m.start()], body[m.start() :]) if m else (body, "")
        d = self.docs.get(rel)
        stem = Path(rel).stem
        backlinks = sorted(
            o.path for o in self.docs.values() if o.path != rel and _links_to(o.links, rel, stem)
        )
        return {
            "path": rel,
            "title": d.title if d else stem,
            "type": d.type if d else note_type(rel, meta),
            "date": d.date if d else "",
            "meta": meta,
            "current": current.strip(),
            "history": history.strip(),
            "truncated": len(raw) > NOTE_CAP,
            "links": d.links if d else [],
            "backlinks": backlinks[:50],
        }

    def by_name(self, name: str) -> str | None:
        """Wikilink target or Cognee document name to a vault path."""
        name = name.strip()
        if not name:
            return None
        if name in self.docs:
            return name
        cand = name.replace("__", "/")
        for c in (cand, cand + ".md", cand + ".txt"):
            if c in self.docs:
                return c
        stem = Path(cand).stem.lower()
        for path in self.docs:
            if Path(path).stem.lower() == stem:
                return path
        return None

    # ---------------------------------------------------------------- graph
    def link_graph(self, center: str, hops: int = 2, cap: int = 120) -> dict[str, Any]:
        """Two hop graph of notes over [[wikilinks]], both directions."""
        self.refresh()
        adj: dict[str, set[str]] = {}
        for d in self.docs.values():
            for target in d.links:
                t = self.by_name(target)
                if t and t != d.path:
                    adj.setdefault(d.path, set()).add(t)
                    adj.setdefault(t, set()).add(d.path)
        if center not in self.docs:
            return {"nodes": [], "edges": [], "center": None}
        seen = {center: 0}
        frontier = [center]
        for depth in range(1, hops + 1):
            nxt = []
            for n in frontier:
                for m in sorted(adj.get(n, ())):
                    if m not in seen and len(seen) < cap:
                        seen[m] = depth
                        nxt.append(m)
            frontier = nxt
        nodes = [
            {
                "id": p,
                "label": self.docs[p].title,
                "kind": _shape(self.docs[p].type),
                "type": self.docs[p].type,
                "path": p,
                "depth": depth,
                "date": self.docs[p].date,
            }
            for p, depth in seen.items()
        ]
        edges = []
        for a in seen:
            for b in adj.get(a, ()):
                if b in seen and a < b:
                    edges.append({"source": a, "target": b, "label": "links"})
        return {"nodes": nodes, "edges": edges, "center": center}

    def stats(self) -> dict[str, Any]:
        return {"notes": len(self.docs), "scan_ms": self.scan_ms, "root": str(self.root)}


def _first_heading(body: str) -> str:
    for line in body.splitlines()[:20]:
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _links_to(links: list[str], rel: str, stem: str) -> bool:
    for link in links:
        if link == rel or link == stem or link + ".md" == rel or Path(link).stem == stem:
            return True
    return False


def _shape(t: str) -> str:
    """Graph node shape class from a note or entity type (guide section 8)."""
    t = t.lower()
    if t in {"decision", "council"}:
        return "decision"
    if t in {"project", "system", "wiki"}:
        return "project"
    if t in {"event", "pattern", "daily", "note"}:
        return "event" if t in {"event", "pattern"} else "note"
    if t in {"god"}:
        return "god"
    return "thing"


shape_for = _shape
