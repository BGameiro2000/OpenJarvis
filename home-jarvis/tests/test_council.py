"""Council: routing, rounds, verdict, saved record, floor link. No model."""

from __future__ import annotations

import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from home_jarvis.brain import ScriptedBrain
from home_jarvis.config import load
from home_jarvis.council import Council, build_router, route, wants_council
from home_jarvis.demo import demo_brain
from home_jarvis.events import EventLog
from home_jarvis.floor import FloorManager

NOON = time.mktime((2026, 10, 22, 12, 0, 0, 0, 0, -1))
NIGHT = time.mktime((2026, 10, 22, 2, 30, 0, 0, 0, -1))


def called(router):
    return {r["god"] for r in router if r["called"]}


def test_router_reasons_for_every_god():
    r = route("Should I buy the A2000 now for 310 euros?", hour=12)
    assert {"tyche", "daedalus", "ananke", "ate"} <= called(r)
    assert len(r) == 8 and all(x["reason"] for x in r)
    r = route("Should I take the job offer?", hour=12)
    assert called(r) == {"hermes"}
    assert route("Should I take the job offer?", hour=2)[3]["called"]  # night calls the pair
    assert called(route("Hmm what about it?", hour=12)) == {"daedalus"}  # default adviser
    assert (
        len(
            called(route("paper review slides talk deadline server build gpu buy price", 12))
            - {"ananke", "ate"}
        )
        == 3
    )


def test_wants_council():
    assert wants_council("Should I get the A2000 now, or RAM first?")
    assert not wants_council("Hi there")


def test_run_saves_a_record_and_cleans_model_output(tmp_path):
    brain = ScriptedBrain(
        asks=[
            {
                "positions": [
                    {"god": "tyche", "stance": "FOR", "confidence": 2, "argument": "Fair price."},
                    {"god": "zeus", "stance": "for", "argument": "not a council member"},
                    {"god": "ate", "stance": "flag", "confidence": 0.7, "argument": "Impulse."},
                ]
            },
            "not json at all",
            {
                "decision": "maybe",
                "confidence": 0.8,
                "summary": "Wait.",
                "dissent": [
                    {"god": "tyche", "argument": "Fair."},
                    {"god": "zeus", "argument": "x"},
                ],
                "flags": [
                    {"god": "ate", "text": "impulse window", "pressure": 0.74},
                    {"god": "tyche", "text": "not a flag god", "pressure": 1},
                ],
            },
        ]
    )
    log = EventLog(clock=lambda: NIGHT)
    c = Council(brain, tmp_path, log, clock=lambda: NIGHT)
    rec = c.run("Should I buy the A2000 now?")
    r1 = rec["rounds"][0]
    assert [p["god"] for p in r1] == ["tyche", "ate"] and r1[0]["confidence"] == 1.0
    assert r1[0]["stance"] == "for" and rec["rounds"][1] == []
    v = rec["verdict"]
    assert v["decision"] == "WAIT" and [d["god"] for d in v["dissent"]] == ["tyche"]
    assert v["flags"] == [{"god": "ate", "text": "impulse window", "pressure": 0.74}]
    assert rec["sim"] == "2026-10-22-0001"
    saved = (tmp_path / "council" / "2026-10-22-0001.md").read_text()
    assert "type: council" in saved and "```json council" in saved
    assert c.get(rec["sim"])["verdict"]["decision"] == "WAIT"
    assert c.list()[0]["sim"] == rec["sim"]
    assert c.run("Should I buy it?")["sim"] == "2026-10-22-0002"  # never overwrites
    kinds = [e.kind for e in log.recent(100)]
    assert kinds[0] == "consult" and "verdict" in kinds and "council_saved" in kinds
    assert c.get("../../etc/passwd") is None


def test_floor_consults_and_root_interjects_on_ate_flag(tmp_path):
    clock = lambda: NIGHT  # noqa: E731
    log = EventLog(clock=clock)
    brain = demo_brain()
    council = Council(brain, tmp_path, log, clock=clock)
    floor = FloorManager(brain, load(None), log, clock=clock, council=council)
    ev = floor.run_turn("Should I get the A2000 now, or do the RAM upgrade first?")
    kinds = [e.kind for e in ev]
    assert kinds.index("consult") < kinds.index("verdict") < kinds.index("say")
    inter = next(e for e in ev if e.kind == "interject")
    assert inter.speaker == "root" and inter.source.startswith("council:")
    assert (tmp_path / "council").is_dir()


def test_council_off_means_no_calls(tmp_path):
    log = EventLog()
    brain = ScriptedBrain(replies={"finch": ["Fine."]})
    council = Council(brain, tmp_path, log)
    cfg = load(None).with_modes(disable=["council"])
    FloorManager(brain, cfg, log, council=council).run_turn("Should I buy the GPU now?")
    assert not [c for c in brain.calls if c[0] == "ask"]
    assert not (tmp_path / "council").exists()


def test_routes(tmp_path):
    app = FastAPI()
    c = Council(demo_brain(), tmp_path, EventLog())
    app.include_router(build_router(c))
    client = TestClient(app)
    r = client.post("/api/custom/council/run", json={"question": "Should I buy the A2000?"})
    events = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]
    assert events[0]["kind"] == "consult" and events[-1]["kind"] == "council_saved"
    sim = events[-1]["data"]["sim"]
    assert client.get("/api/custom/council/sims").json()["sims"][0]["sim"] == sim
    assert client.get(f"/api/custom/council/sims/{sim}").json()["verdict"]["decision"] == "WAIT"
    assert client.get("/api/custom/council/sims/nope").status_code == 404
