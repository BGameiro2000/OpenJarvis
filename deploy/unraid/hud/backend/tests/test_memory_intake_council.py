"""Memory search and graph, intake board, council replays. No network."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from machine_hud.app import create_app
from machine_hud.memory import fold_graph, rrf, upload_name
from machine_hud.settings import Settings
from machine_hud.vault import VaultIndex, fts_query, parse_frontmatter

DEMO_VAULT = Path(__file__).resolve().parents[2] / "demo-vault"


def sse(text: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]


def make_vault(tmp: Path) -> Path:
    v = tmp / "vault"
    shutil.copytree(DEMO_VAULT, v)
    return v


class FakeCognee:
    """Cognee search and graph over the demo vault's home server note."""

    def __init__(self, up: bool = True) -> None:
        self.up = up
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req.url.path)
        if not self.up:
            raise httpx.ConnectError("down")
        path = req.url.path
        if path == "/api/v1/search":
            body = json.loads(req.content)
            assert body["search_type"] == "CHUNKS"  # no model call
            return httpx.Response(
                200,
                json=[
                    {
                        "search_result": [
                            {
                                "text": "RAM upgrade to 64 GB removes swap spikes",
                                "document_name": "wiki__home-server.md",
                                "score": 0.9,
                            },
                            {
                                "text": "listing at 310 euros",
                                "document_name": "shopping__watch.md",
                                "score": 0.7,
                            },
                            {
                                "text": "an entity only in the graph",
                                "document_name": "not-in-vault",
                                "score": 0.5,
                            },
                        ],
                        "dataset_id": None,
                        "dataset_name": "vault",
                    }
                ],
            )
        if path == "/api/v1/datasets":
            return httpx.Response(200, json=[{"id": "ds1", "name": "vault"}])
        if path == "/api/v1/datasets/ds1/graph":
            return httpx.Response(
                200,
                json={
                    "nodes": [
                        {
                            "id": "d1",
                            "label": "wiki__home-server.md",
                            "type": "TextDocument",
                            "properties": {},
                        },
                        {"id": "c1", "label": "chunk", "type": "DocumentChunk", "properties": {}},
                        {"id": "e1", "label": "RTX A2000", "type": "Entity", "properties": {}},
                        {"id": "e2", "label": "Cognee", "type": "Entity", "properties": {}},
                        {"id": "t1", "label": "product", "type": "EntityType", "properties": {}},
                        {
                            "id": "d2",
                            "label": "shopping__watch.md",
                            "type": "TextDocument",
                            "properties": {},
                        },
                        {"id": "c2", "label": "chunk", "type": "DocumentChunk", "properties": {}},
                    ],
                    "edges": [
                        {"source": "c1", "target": "d1", "label": "is_part_of"},
                        {"source": "c1", "target": "e1", "label": "contains"},
                        {"source": "c1", "target": "e2", "label": "contains"},
                        {"source": "e1", "target": "t1", "label": "is_a"},
                        {"source": "c2", "target": "d2", "label": "is_part_of"},
                        {"source": "c2", "target": "e1", "label": "contains"},
                    ],
                },
            )
        return httpx.Response(404)


def client(tmp: Path, cognee: FakeCognee | None = None, **kw):
    vault = make_vault(tmp)
    s = Settings(
        openjarvis_url="http://oj:8000",
        openjarvis_key="k",
        cognee_url="http://cg:8000",
        cognee_key="ck",
        static_dir=tmp,
        vault=vault,
        demo_vault=vault,
        **kw,
    )
    cg = cognee or FakeCognee()

    def route(req: httpx.Request) -> httpx.Response:
        if req.url.host == "cg":
            return cg(req)
        return httpx.Response(404, json={"detail": "Not Found"})

    app = create_app(s, transport=httpx.MockTransport(route), demo_pace=0)
    return TestClient(app), cg, vault


# --------------------------------------------------------------------- vault
def test_frontmatter_and_query_are_safe():
    meta, body = parse_frontmatter("---\ntype: decision\ndate: 2026-10-21\n---\n# T\nx")
    assert meta == {"type": "decision", "date": "2026-10-21"} and body.startswith("# T")
    assert fts_query('ram" OR 1=1; drop') == '"ram"* "OR"* "1"* "1"* "drop"*'
    assert fts_query("   ") == ""


def test_vault_index_search_filters_note_and_traversal(tmp_path):
    v = VaultIndex(make_vault(tmp_path))
    hits = v.search("RAM upgrade")
    assert hits and hits[0]["path"] == "wiki/home-server.md"
    assert v.search("RAM", types={"decision"})[0]["type"] == "decision"
    assert all(h["path"].split("/")[0] != "wiki" for h in v.search("RAM", exclude=["wiki"]))
    assert not v.search("RAM", date_from="2030-01-01")
    note = v.note("wiki/home-server.md")
    assert "Current truth" in note["current"] and note["history"].startswith("## History")
    assert "projects/home-jarvis.md" in note["backlinks"]
    assert v.note("../../etc/passwd") is None and v.note("/etc/passwd") is None
    assert v.by_name("wiki__home-server.md") == "wiki/home-server.md"
    assert v.by_name("watch") == "shopping/watch.md"


def test_vault_index_picks_up_changes(tmp_path):
    vault = make_vault(tmp_path)
    v = VaultIndex(vault, refresh_s=0)
    assert not v.search("zeppelin")
    (vault / "ideas" / "zep.md").write_text("# Zeppelin\nA zeppelin idea.")
    assert v.search("zeppelin")[0]["path"] == "ideas/zep.md"
    (vault / "ideas" / "zep.md").unlink()
    assert not v.search("zeppelin")


# -------------------------------------------------------------------- memory
def test_rrf_and_upload_name():
    s = rrf({"a": ["x", "y"], "b": ["y"]})
    assert s["y"] > s["x"]
    assert upload_name("wiki/home-server.md") == "wiki__home-server.md"


def test_hybrid_search_tags_both_vault_graph(tmp_path):
    c, cg, _ = client(tmp_path)
    r = c.get("/api/memory/search", params={"q": "RAM upgrade"}).json()
    tags = {h["key"]: h["tag"] for h in r["hits"]}
    assert tags["wiki/home-server.md"] == "BOTH"
    assert tags["graph:not-in-vault"] == "GRAPH"
    assert r["sources"]["vault"]["ok"] and r["sources"]["graph"]["ok"]
    assert r["hits"][0]["key"] == "wiki/home-server.md"  # in both lists, ranks first
    vault_only = c.get("/api/memory/search", params={"q": "RAM", "mode": "vault"}).json()
    assert all(h["tag"] == "VAULT" for h in vault_only["hits"])
    assert c.get("/api/memory/search", params={"q": "x", "mode": "bad"}).status_code == 400


def test_search_survives_cognee_down(tmp_path):
    c, _, _ = client(tmp_path, cognee=FakeCognee(up=False))
    r = c.get("/api/memory/search", params={"q": "RAM upgrade"}).json()
    assert r["hits"] and all(h["tag"] == "VAULT" for h in r["hits"])
    assert r["sources"]["graph"]["ok"] is False
    assert "not reachable" in r["sources"]["graph"]["reason"]
    g = c.get("/api/graph", params={"path": "wiki/home-server.md"}).json()
    assert g["via"] == "wikilinks" and "not reachable" in g["reason"]
    assert any(n["path"] == "projects/home-jarvis.md" for n in g["nodes"])


def test_cognee_graph_folds_chunks_and_is_stable(tmp_path):
    c, _, _ = client(tmp_path)
    g1 = c.get("/api/graph", params={"path": "wiki/home-server.md"}).json()
    assert g1["via"] == "cognee" and g1["ok"]
    labels = {n["label"] for n in g1["nodes"]}
    assert {"RTX A2000", "Cognee"} <= labels and "chunk" not in labels and "product" not in labels
    assert any(n["path"] == "shopping/watch.md" for n in g1["nodes"])  # two hops via the A2000
    a2000 = next(n for n in g1["nodes"] if n["label"] == "RTX A2000")
    assert a2000["kind"] == "project" and a2000["type"] == "product"
    g2 = c.get("/api/graph", params={"path": "wiki/home-server.md"}).json()
    assert [n["id"] for n in g1["nodes"]] == [n["id"] for n in g2["nodes"]]


def test_fold_graph_turns_chunks_into_mentions():
    nodes, adj, labels = fold_graph(
        {
            "nodes": [
                {"id": "d", "type": "TextDocument"},
                {"id": "c", "type": "DocumentChunk"},
                {"id": "e", "type": "Entity"},
            ],
            "edges": [{"source": "c", "target": "d"}, {"source": "c", "target": "e"}],
        }
    )
    assert set(nodes) == {"d", "e"} and adj["d"] == {"e"} and labels[("d", "e")] == "mentions"


def test_note_endpoint(tmp_path):
    c, _, _ = client(tmp_path)
    n = c.get("/api/memory/note", params={"path": "decisions/2026-10-21-gpu-wait.md"}).json()
    assert n["type"] == "decision" and n["obsidian"].endswith("decisions%2F2026-10-21-gpu-wait.md")
    assert c.get("/api/memory/note", params={"path": "../secret.md"}).status_code == 404


# -------------------------------------------------------------------- intake
def test_intake_board_reads_vault_without_sorter(tmp_path):
    c, _, vault = client(tmp_path)
    log = vault / "inbox" / ".decision-log.jsonl"
    today = time.strftime("%Y-%m-%d")
    log.write_text(
        json.dumps(
            {
                "ts": f"{today}T09:00:00",
                "source": "a.md",
                "decision": "filed",
                "route": "gtd_action",
                "dry_run": False,
            }
        )
        + "\nnot json\n"
        + json.dumps(
            {
                "ts": f"{today}T09:01:00",
                "source": "2026-10-22-note-with-markup.md",
                "decision": "escalated",
                "dry_run": False,
            }
        )
        + "\n"
    )
    b = c.get("/api/intake").json()
    assert [i["name"] for i in b["inbox"]] == ["2026-10-22-note-with-markup.md"]
    assert b["log"][0]["decision"] == "escalated" and len(b["log"]) == 2
    assert b["summary"] == {
        "inbox": 1,
        "filed_today": 1,
        "escalated": 1,
        "routes_today": {"gtd_action": 1, "escalate": 1},
    }
    assert b["sorter"] is None and "sorter not loaded" in b["sorter_reason"]
    assert c.post("/api/intake/scan", json={"apply": False}).status_code == 503
    assert c.post("/api/intake/decide", json={"id": "x", "action": "burn"}).status_code == 400


def test_demo_intake_dry_run_apply_and_decisions(tmp_path):
    c, _, vault = client(tmp_path, demo=True)
    before = sorted(p.as_posix() for p in vault.rglob("*"))
    b = c.get("/api/intake").json()
    assert b["sorter"]["dry_run"] and b["summary"]["escalated"] == 3
    plan = c.post("/api/intake/scan", json={"apply": False}).json()
    assert plan["dry_run"] and plan["summary"]["escalate"] == 3
    chat = next(i for i in plan["items"] if i["kind"] == "chat")
    assert chat["decided_by"] == "rule:chat" and chat["decision"] == "file"
    low = next(i for i in plan["items"] if i["source"] == "thoughts.md")
    assert low["decision"] == "escalate" and "confidence" in low["reason"]
    assert len(c.get("/api/intake").json()["inbox"]) == 7  # dry run moved nothing

    plan = c.post("/api/intake/scan", json={"apply": True}).json()
    b = c.get("/api/intake").json()
    assert len(b["inbox"]) == 3 and b["summary"]["filed_today"] == 4
    pend = {p["source"]: p["id"] for p in b["sorter"]["pending"]}
    r = c.post("/api/intake/decide", json={"id": pend["thoughts.md"], "action": "file"})
    assert r.json()["decision"] == "filed"
    r = c.post("/api/intake/decide", json={"id": pend["voice-memo-0412.m4a"], "action": "file"})
    assert r.status_code == 400  # an audio without a transcript cannot be filed as is
    r = c.post(
        "/api/intake/decide",
        json={
            "id": pend["2026-10-22-note-with-markup.md"],
            "action": "add_list",
            "list": {"name": "tests.md", "route": "reference", "scope": "test notes"},
        },
    )
    assert r.json()["decision"] == "filed"
    assert any(m["list"] == "tests.md" for m in c.get("/api/intake").json()["sorter"]["menu"])
    assert (
        c.post(
            "/api/intake/decide", json={"id": pend["voice-memo-0412.m4a"], "action": "leave"}
        ).json()["decision"]
        == "left"
    )
    assert sorted(p.as_posix() for p in vault.rglob("*")) == before  # the HUD wrote nothing


def test_demo_capture_note_and_upload(tmp_path):
    c, _, _ = client(tmp_path, demo=True)
    r = c.post("/api/intake/note", json={"text": "Call Ana about the weekend"}).json()
    assert r["saved"][0].startswith("inbox/")
    r = c.post(
        "/api/intake/upload", files={"file": ("../../evil memo.m4a", b"\x00\x01", "audio/mp4")}
    )
    assert r.json()["saved"] == ["inbox/evil memo.m4a"]
    kinds = {i["name"]: i["kind"] for i in c.get("/api/intake").json()["inbox"]}
    assert kinds["evil memo.m4a"] == "audio"
    assert (
        c.post(
            "/api/intake/upload", content=b"x", headers={"content-type": "text/plain"}
        ).status_code
        == 400
    )


# ------------------------------------------------------------------- council
def test_council_replays_from_vault(tmp_path):
    c, _, _ = client(tmp_path)
    sims = c.get("/api/council").json()
    assert sims["sims"][0]["sim"] == "2026-10-21-0381" and len(sims["gods"]) == 8
    rec = c.get("/api/council/2026-10-21-0381").json()
    assert rec["verdict"]["decision"] == "WAIT"
    called = {r["god"] for r in rec["router"] if r["called"]}
    assert called == {"tyche", "daedalus", "ananke", "ate"}
    assert all(r["reason"] for r in rec["router"])  # uncalled gods say why
    assert c.get("/api/council/..%2F..%2Fetc").status_code == 404
    evs = sse(c.post("/api/council/run", json={"question": "x"}).text)
    assert evs[-1]["kind"] == "error" and "not loaded" in evs[-1]["text"]


def test_demo_council_run_and_floor_consults(tmp_path):
    c, _, _ = client(tmp_path, demo=True)
    evs = sse(c.post("/api/council/run", json={"question": "Should I buy the A2000?"}).text)
    kinds = [e["kind"] for e in evs]
    assert kinds[0] == "consult" and kinds[-1] == "council_saved" and "verdict" in kinds
    assert kinds.count("position") == 6
    sim = evs[-1]["data"]["sim"]
    assert c.get(f"/api/council/{sim}").json()["verdict"]["decision"] == "WAIT"
    turn = sse(c.post("/api/chat", json={"text": "Should I get the A2000 now?"}).text)
    k = [e["kind"] for e in turn]
    assert k.index("consult") < k.index("verdict") < k.index("interject")


@pytest.mark.parametrize("q", ["<script>", '" OR', "*", "'"])
def test_search_handles_odd_queries(tmp_path, q):
    c, _, _ = client(tmp_path)
    assert c.get("/api/memory/search", params={"q": q, "mode": "vault"}).status_code == 200


def test_first_search_maps_graph_hits(tmp_path):
    # The index must be built before graph hits are mapped to vault paths.
    c, _, _ = client(tmp_path, demo=True)
    r = c.get("/api/memory/search", params={"q": "RAM upgrade"}).json()
    assert "BOTH" in {h["tag"] for h in r["hits"]}
