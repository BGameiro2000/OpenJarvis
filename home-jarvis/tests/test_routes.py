"""HTTP routes with the demo brain, through FastAPI's test client."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from home_jarvis.config import load
from home_jarvis.demo import demo_brain
from home_jarvis.floor import FloorManager
from home_jarvis.routes import PREFIX, build_router


def client():
    app = FastAPI()
    floor = FloorManager(demo_brain(), load(None))
    app.include_router(build_router(floor))
    return TestClient(app), floor


def sse(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_chat_streams_the_turn():
    c, _ = client()
    r = c.post(f"{PREFIX}/chat", json={"text": "Should I get the A2000 now, or RAM first?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse(r)
    k = [e["kind"] for e in events]
    assert k[0] == "turn" and k[-1] == "turn"
    assert "interject" in k and "yield" in k
    inter = next(e for e in events if e["kind"] == "interject")
    assert inter["speaker"] == "root" and inter["voice"] == "root-v1"


def test_build_scenario_and_state_and_config():
    c, _ = client()
    events = sse(c.post(f"{PREFIX}/chat", json={"text": "How should I structure the paper?"}))
    assert [e["speaker"] for e in events if e["kind"] == "build"] == ["root", "finch"]
    st = c.get(f"{PREFIX}/state").json()
    assert st["profile"] == "home_text" and st["turn"] == 1
    st = c.post(f"{PREFIX}/config", json={"profile": "duet"}).json()
    assert st["profile"] == "duet"
    assert c.post(f"{PREFIX}/config", json={"enable": ["interrupt"]}).status_code == 400
    assert c.post(f"{PREFIX}/mute", json={"persona": "root", "minutes": 5}).json()["muted"]
    assert c.post(f"{PREFIX}/mute", json={"persona": "bob"}).status_code == 400
