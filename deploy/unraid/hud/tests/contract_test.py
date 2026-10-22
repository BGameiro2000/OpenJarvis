"""Contract test: the HUD backend against the real custom package.

Runs the HUD backend with OpenJarvis replaced by a FastAPI app that mounts
the real floor, intake and council routers from the ``home-jarvis`` branch
(demo brain and demo classifier, a temporary vault). Proves the HUD and the
package agree on every shape in docs/DESIGN.md section 4.

    HOME_JARVIS_SRC=/path/to/home-jarvis HOOKS_SRC=deploy/unraid/custom \\
      python -m pytest -q deploy/unraid/hud/tests/contract_test.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = os.environ.get("HOME_JARVIS_SRC", "")
if not SRC:
    pytest.skip(
        "set HOME_JARVIS_SRC to a home-jarvis checkout", allow_module_level=True
    )
sys.path[:0] = [
    SRC,
    os.environ.get("HOOKS_SRC", str(ROOT.parent / "custom")),
    str(ROOT / "backend"),
]

from home_jarvis.config import load  # noqa: E402
from home_jarvis.council import Council  # noqa: E402
from home_jarvis.council import build_router as council_router  # noqa: E402
from home_jarvis.demo import demo_brain  # noqa: E402
from home_jarvis.events import EventLog  # noqa: E402
from home_jarvis.floor import FloorManager  # noqa: E402
from home_jarvis.intake.classify import demo_classifier  # noqa: E402
from home_jarvis.intake.routes import build_router as intake_router  # noqa: E402
from home_jarvis.intake.sorter import Sorter  # noqa: E402
from home_jarvis.routes import build_router as floor_router  # noqa: E402
from machine_hud.app import create_app  # noqa: E402
from machine_hud.settings import Settings  # noqa: E402

WHATSAPP = "12/10/2026, 21:04 - Ana: Dinner on Friday?\n12/10/2026, 21:05 - Bernardo: Yes, 20:00\n"
CLAUDE = [
    {
        "uuid": "c1",
        "name": "GPU vs RAM",
        "created_at": "2026-10-20T10:00:00Z",
        "chat_messages": [
            {
                "sender": "human",
                "text": "A2000 or RAM?",
                "created_at": "2026-10-20T10:00:00Z",
            },
            {
                "sender": "assistant",
                "text": "RAM first.",
                "created_at": "2026-10-20T10:00:05Z",
            },
        ],
    }
]


def sse(text: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]


@pytest.fixture
def hud(tmp_path):
    vault = tmp_path / "vault"
    (vault / "inbox").mkdir(parents=True)
    (vault / "inbox" / "plumber.md").write_text(
        "Call the plumber about the boiler tomorrow morning.\n"
    )
    (vault / "inbox" / "WhatsApp Chat with Ana.txt").write_text(WHATSAPP)
    (vault / "inbox" / "conversations.json").write_text(json.dumps(CLAUDE))
    (vault / "inbox" / "memo.m4a").write_bytes(b"\x00\x00\x00\x18ftypM4A ")
    log = EventLog()
    brain = demo_brain()
    council = Council(brain, vault, log)
    floor = FloorManager(brain, load(None), log, council=council)
    sorter = Sorter(
        vault,
        config_path=tmp_path / "intake.toml",
        classifier=demo_classifier(),
        log=log,
    )
    oj = FastAPI()
    oj.include_router(floor_router(floor))
    oj.include_router(intake_router(sorter, log))
    oj.include_router(council_router(council))
    oj.get("/health")(lambda: {"status": "ok"})
    oj.get("/v1/info")(lambda: {"engine": "cloud", "model": "test"})
    s = Settings(
        openjarvis_url="http://oj",
        openjarvis_key="k",
        cognee_url="http://cg",
        static_dir=tmp_path,
        vault=vault,
    )
    app = create_app(s, transport=httpx.ASGITransport(app=oj), demo_pace=0)
    return TestClient(app), vault


def test_board_scan_apply_and_decide(hud):
    c, vault = hud
    b = c.get("/api/intake").json()
    assert b["sorter"] and b["sorter"]["dry_run"] is True, b["sorter_reason"]
    assert {i["name"] for i in b["sorter"]["inbox"]} == {
        "plumber.md",
        "WhatsApp Chat with Ana.txt",
        "conversations.json",
        "memo.m4a",
    }
    assert {m["list"] for m in b["sorter"]["menu"]} >= {
        "next-actions.md",
        "chats.md",
        "ai-chats.md",
    }

    plan = c.post("/api/intake/scan", json={"apply": False}).json()
    assert plan["dry_run"] is True
    keys = {
        "id",
        "source",
        "kind",
        "title",
        "count",
        "preview",
        "decided_by",
        "envelope",
        "checks",
        "decision",
        "actions",
        "reason",
    }
    for it in plan["items"]:
        assert keys <= set(it), set(it) ^ keys
    by_src = {it["source"]: it for it in plan["items"]}
    assert by_src["WhatsApp Chat with Ana.txt"]["decided_by"] == "rule:chat"
    assert by_src["conversations.json"]["decided_by"] == "rule:ai_chat"
    assert by_src["memo.m4a"]["decision"] == "escalate"
    assert (vault / "inbox" / "plumber.md").exists()  # dry run moved nothing

    plan = c.post("/api/intake/scan", json={"apply": True}).json()
    assert plan["dry_run"] is False
    b = c.get("/api/intake").json()
    assert b["summary"]["filed_today"] >= 2 and b["log"], b["summary"]
    assert {i["name"] for i in b["inbox"]} >= {"memo.m4a"}
    pending = {p["source"]: p for p in b["sorter"]["pending"]}
    memo = pending["memo.m4a"]
    r = c.post("/api/intake/decide", json={"id": memo["id"], "action": "file"})
    assert r.status_code in (400, 422) and isinstance(r.json()["detail"], str), r.json()
    r = c.post("/api/intake/decide", json={"id": memo["id"], "action": "leave"})
    assert r.status_code == 200, r.text


def test_capture_note_and_upload(hud):
    c, vault = hud
    r = c.post(
        "/api/intake/note",
        json={"text": "Idea: a timeline on the graph", "why": "test"},
    )
    assert r.status_code == 200 and r.json()["saved"][0].startswith("inbox/"), r.text
    r = c.post(
        "/api/intake/upload",
        files={"file": ("notes from talk.md", b"# Talk\nnotes", "text/markdown")},
    )
    assert r.status_code == 200, r.text
    saved = r.json()["saved"]
    assert saved and (vault / saved[0]).exists()


def test_council_run_and_replay(hud):
    c, vault = hud
    evs = sse(
        c.post(
            "/api/council/run", json={"question": "Should I buy the A2000 now?"}
        ).text
    )
    kinds = [e["kind"] for e in evs]
    assert (
        kinds[0] == "consult" and kinds[-1] == "council_saved" and "verdict" in kinds
    ), kinds
    sim = evs[-1]["data"]["sim"]
    listed = c.get("/api/council").json()["sims"]
    assert listed[0]["sim"] == sim and listed[0]["verdict"] == "WAIT"
    rec = c.get(f"/api/council/{sim}").json()
    assert len(rec["router"]) == 8 and rec["rounds"][0] and rec["verdict"]["flags"]


def test_floor_turn_consults_and_root_cuts_in(hud):
    c, _ = hud
    assert c.get("/api/status").json()["mode"] == "floor"
    evs = sse(
        c.post(
            "/api/chat", json={"text": "Should I get the A2000 now, or RAM first?"}
        ).text
    )
    kinds = [e["kind"] for e in evs]
    assert "consult" in kinds and "interject" in kinds, kinds
    inter = next(e for e in evs if e["kind"] == "interject")
    assert inter["speaker"] == "root" and inter["source"].startswith("council:")
    assert c.post("/api/floor/config", json={"disable": ["council"]}).status_code == 200
