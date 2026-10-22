"""Intake HTTP routes through FastAPI's test client, with the demo classifier."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from home_jarvis.events import EventLog
from home_jarvis.intake import IntakeConfig, Sorter, demo_classifier
from home_jarvis.intake import routes as intake_routes
from home_jarvis.intake.routes import PREFIX, build_router
from test_intake_sources import ANDROID, make_zip


def client(tmp_path):
    (tmp_path / "inbox").mkdir()
    log = EventLog()
    sorter = Sorter(tmp_path, config=IntakeConfig(), classifier=demo_classifier())
    app = FastAPI()
    app.include_router(build_router(sorter, log))
    return TestClient(app), sorter, log


def test_state_shape(tmp_path):
    c, _, _ = client(tmp_path)
    (tmp_path / "inbox" / "voice-0412.m4a").write_bytes(b"\x00\x00\x00\x20ftypM4A x")
    st = c.get(f"{PREFIX}/state").json()
    assert set(st) == {
        "dry_run",
        "min_confidence",
        "routes",
        "menu",
        "inbox",
        "pending",
        "transcription",
        "last_scan",
    }
    assert st["dry_run"] is True and st["min_confidence"] == 0.6
    assert st["routes"] == ["project_shaped", "gtd_action", "idea_capture", "reference", "escalate"]
    assert st["menu"][0] == {
        "list": "next-actions.md",
        "route": "gtd_action",
        "folder": "gtd",
        "scope": "single next steps Bernardo can do himself",
    }
    [f] = st["inbox"]
    assert f["name"] == "voice-0412.m4a" and f["kind"] == "audio" and len(f["sha256"]) == 64
    assert set(f) == {"name", "size", "mtime", "kind", "sha256"}
    assert st["transcription"] == {"available": False, "backend": ""}
    assert st["last_scan"] is None and st["pending"] == []


def test_capture_scan_decide_flow(tmp_path):
    c, _, log = client(tmp_path)
    r = c.post(
        f"{PREFIX}/capture",
        files=[
            ("file", ("../../WhatsApp Chat with Ana Silva.txt", ANDROID.encode(), "text/plain")),
            ("file", ("notes.zip", make_zip({"plan.md": b"Some musing"}), "application/zip")),
        ],
    )
    assert r.status_code == 200
    assert r.json() == {"saved": ["inbox/WhatsApp Chat with Ana Silva.txt", "inbox/notes.zip"]}
    r = c.post(f"{PREFIX}/capture", json={"text": "Buy milk", "why": "quick note"})
    assert r.status_code == 200 and r.json()["saved"][0].startswith("inbox/note-")

    plan = c.post(f"{PREFIX}/scan", json={"apply": False}).json()
    assert set(plan) == {"dry_run", "at", "summary", "items"} and plan["dry_run"] is True
    item_keys = {
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
    for item in plan["items"]:
        assert item_keys <= set(item)
        assert set(item["envelope"]) == {
            "route",
            "slug",
            "confidence",
            "tags",
            "target_list",
            "why",
        }
    assert plan["summary"] == {"reference": 1, "gtd_action": 1, "escalate": 1}
    esc = next(i for i in plan["items"] if i["decision"] == "escalate")
    assert esc["source"] == "notes.zip" and esc["checks"]
    st = c.get(f"{PREFIX}/state").json()
    assert [p["id"] for p in st["pending"]] == [esc["id"]]
    assert st["last_scan"]["dry_run"] is True

    applied = c.post(f"{PREFIX}/scan", json={"apply": True}).json()
    assert applied["dry_run"] is False
    assert (tmp_path / "reference" / "chats" / "ana-silva-whatsapp.md").exists()

    bad = c.post(f"{PREFIX}/decide", json={"id": esc["id"], "action": "edit"})
    assert bad.status_code == 400
    bad = c.post(
        f"{PREFIX}/decide",
        json={"id": esc["id"], "action": "edit", "envelope": {**esc["envelope"], "route": "x"}},
    )
    assert bad.status_code == 422 and bad.json()["detail"]["checks"]
    r = c.post(
        f"{PREFIX}/decide",
        json={
            "id": esc["id"],
            "action": "add_list",
            "list": {"name": "musings.md", "route": "idea_capture", "scope": "musings"},
        },
    )
    assert r.status_code == 200 and r.json()["ok"]
    assert (tmp_path / "ideas" / "musings.md").exists()
    assert c.get(f"{PREFIX}/state").json()["pending"] == []
    assert c.post(f"{PREFIX}/decide", json={"id": "nope", "action": "leave"}).status_code == 404

    tail = c.get(f"{PREFIX}/log?n=5").json()
    assert tail[-1]["source"] == "notes.zip" and tail[-1]["by"] == "hud"
    kinds = {e.kind for e in log.recent()}
    assert {"route", "escalate", "file"} <= kinds


def test_capture_rejects_bad_requests(tmp_path, monkeypatch):
    c, _, _ = client(tmp_path)
    assert c.post(f"{PREFIX}/capture", json={"nope": 1}).status_code == 400
    assert c.post(f"{PREFIX}/capture", json={"text": "  "}).status_code == 400
    assert c.post(f"{PREFIX}/capture", content=b"not json").status_code == 400
    r = c.post(f"{PREFIX}/capture", files={"other": ("a.md", b"x", "text/plain")})
    assert r.status_code == 400
    monkeypatch.setattr(intake_routes, "MAX_CAPTURE", 10)
    r = c.post(f"{PREFIX}/capture", files={"file": ("big.md", b"x" * 100, "text/plain")})
    assert r.status_code == 413
    assert not (tmp_path / "inbox" / "big.md").exists()


def test_scan_with_broken_config(tmp_path):
    c, sorter, _ = client(tmp_path)
    path = tmp_path / "intake.toml"
    path.write_text('[[lists]]\nlist = "../bad.md"\nroute = "reference"\n')
    sorter._config, sorter.config_path = None, path
    r = c.post(f"{PREFIX}/scan", json={})
    assert r.status_code == 400 and "intake.toml" in json.dumps(r.json())


def test_server_hook_mounts_intake(tmp_path, monkeypatch):
    import home_jarvis

    from openjarvis.core.events import EventBus

    monkeypatch.setenv("OPENJARVIS_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("OPENJARVIS_CUSTOM_DIR", str(tmp_path / "custom"))
    monkeypatch.setenv("HOME_JARVIS_DEMO", "1")
    app = FastAPI()
    app.state.bus = EventBus()
    home_jarvis._start(app)
    c = TestClient(app)
    r = c.post(f"{PREFIX}/capture", json={"text": "Buy milk"})
    assert r.status_code == 200
    plan = c.post(f"{PREFIX}/scan", json={}).json()
    assert plan["dry_run"] is True and plan["summary"] == {"gtd_action": 1}
    assert home_jarvis.SORTER.vault == tmp_path / "vault"
    assert any(e.kind == "route" for e in home_jarvis.FLOOR.log.recent())
