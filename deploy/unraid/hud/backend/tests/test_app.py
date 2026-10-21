"""HUD backend tests. No network: upstream is an httpx MockTransport."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from machine_hud.app import create_app
from machine_hud.events import Hub, make_event
from machine_hud.settings import Settings

KEY = "oj-dummy-key-not-real"
CKEY = "cg-dummy-key-not-real"


def sse(text: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]


def settings(tmp_path: Path, **kw) -> Settings:
    base = dict(
        openjarvis_url="http://oj:8000",
        openjarvis_key=KEY,
        cognee_url="http://cg:8000",
        cognee_key=CKEY,
        static_dir=tmp_path,
    )
    base.update(kw)
    return Settings(**base)


def stream_body() -> bytes:
    chunk = lambda t: "data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n"  # noqa: E731
    parts = [
        'event: agent_turn_start\ndata: {"agent": "orchestrator"}\n\n',
        'event: tool_call_start\ndata: {"tool": "cognee_recall", "arguments": "{}"}\n\n',
        "event: tool_call_end\n"
        'data: {"tool": "cognee_recall", "success": true, "latency": 0.31}\n\n',
        chunk("First sen"),
        chunk("tence. Second"),
        chunk(" one! Third"),
        "data: [DONE]\n\n",
    ]
    return "".join(parts).encode()


class Upstream:
    """Fake OpenJarvis and Cognee. Records the headers it saw."""

    def __init__(self, floor: bool = False, cognee: bool = True) -> None:
        self.floor = floor
        self.cognee = cognee
        self.seen: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        host, path = req.url.host, req.url.path
        if host == "cg":
            if not self.cognee:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"status": "ok"})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/info":
            return httpx.Response(200, json={"engine": "cloud", "model": "claude-sonnet-5-5"})
        if path.startswith("/api/custom/floor"):
            if not self.floor:
                return httpx.Response(404, json={"detail": "Not Found"})
            if path.endswith("/state"):
                return httpx.Response(200, json={"profile": "home_text", "enabled": ["interrupt"]})
            if path.endswith("/chat"):
                evs = [
                    {"id": "ev_000001", "t": 1.0, "kind": "turn", "text": "hi"},
                    {
                        "id": "ev_000002",
                        "t": 1.1,
                        "kind": "interject",
                        "speaker": "root",
                        "text": "Wait.",
                    },
                    {"id": "ev_000003", "t": 1.2, "kind": "turn", "data": {"phase": "end"}},
                ]
                body = "".join(f"data: {json.dumps(e)}\n\n" for e in evs)
                return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
            if path.endswith("/mute"):
                return httpx.Response(200, json={"muted": {"root": 30}})
        if path == "/v1/chat/completions":
            return httpx.Response(
                200, content=stream_body(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(404)


def client(tmp_path, up=None, **kw) -> tuple[TestClient, Upstream]:
    up = up or Upstream()
    app = create_app(settings(tmp_path, **kw), transport=httpx.MockTransport(up), demo_pace=0)
    return TestClient(app), up


# ------------------------------------------------------------------ security
def test_auth_off_basic_and_tailscale(tmp_path):
    c, _ = client(tmp_path, auth="basic", password="pw")
    assert c.get("/api/status").status_code == 401
    assert c.get("/healthz").status_code == 200
    good = "Basic " + base64.b64encode(b"bernardo:pw").decode()
    bad = "Basic " + base64.b64encode(b"bernardo:nope").decode()
    assert c.get("/api/status", headers={"Authorization": bad}).status_code == 401
    assert c.get("/api/status", headers={"Authorization": good}).status_code == 200
    c, _ = client(tmp_path, auth="tailscale")
    assert c.get("/api/status").status_code == 401
    r = c.get("/api/status", headers={"Tailscale-User-Login": "b@example.org"})
    assert r.status_code == 200 and r.json()["link"] == "tailscale"
    c, _ = client(tmp_path, auth="nonsense")
    assert c.get("/api/status").status_code == 401  # unknown mode fails closed


def test_csp_and_no_key_in_responses(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    c, up = client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200 and "default-src 'self'" in r.headers["content-security-policy"]
    assert "unsafe-inline" not in r.headers["content-security-policy"]
    bodies = [c.get("/api/status").text, c.post("/api/chat", json={"text": "Hello?"}).text]
    for b in bodies:
        assert KEY not in b and CKEY not in b
    # ...but the keys did go upstream
    oj = [q for q in up.seen if q.url.host == "oj"]
    cg = [q for q in up.seen if q.url.host == "cg"]
    assert all(q.headers["authorization"] == f"Bearer {KEY}" for q in oj)
    assert all(q.headers["x-api-key"] == CKEY for q in cg)


def test_spa_fallback_and_no_traversal(tmp_path):
    (tmp_path / "index.html").write_text("INDEX")
    (tmp_path.parent / "secret.txt").write_text("SECRET")
    c, _ = client(tmp_path)
    assert c.get("/memory").text == "INDEX"
    assert "SECRET" not in c.get("/../secret.txt").text
    assert "SECRET" not in c.get("/%2e%2e/secret.txt").text
    assert c.get("/api/nope").status_code == 404


# ---------------------------------------------------------------- plain chat
def test_plain_chat_turns_stream_into_hud_events(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/api/status").json()["mode"] == "plain"
    evs = sse(c.post("/api/chat", json={"text": "What is on today?"}).text)
    kinds = [
        (e["kind"], (e.get("data") or {}).get("state") or (e.get("data") or {}).get("phase"))
        for e in evs
    ]
    assert kinds[0] == ("turn", "start") and kinds[-1] == ("turn", "end")
    assert ("state", "THINKING") in kinds and ("state", "SPEAKING") in kinds
    assert kinds.index(("state", "THINKING")) < kinds.index(("state", "SPEAKING"))
    tool_end = next(e for e in evs if e["kind"] == "tool" and e["data"]["phase"] == "end")
    assert tool_end["data"]["latency"] == 0.31 and tool_end["text"] == "cognee_recall"
    says = [e["text"] for e in evs if e["kind"] == "say"]
    assert says == ["First sentence.", "Second one!", "Third"]
    assert all(e["speaker"] == "finch" for e in evs if e["kind"] == "say")


def test_status_survives_cognee_down(tmp_path):
    c, _ = client(tmp_path, up=Upstream(cognee=False))
    st = c.get("/api/status").json()
    assert st["services"]["openjarvis"]["ok"] and not st["services"]["cognee"]["ok"]
    assert st["engine"] == "cloud" and st["cameras"] == "off"


# ---------------------------------------------------------------- floor mode
def test_floor_mode_relays_floor_events(tmp_path):
    c, up = client(tmp_path, up=Upstream(floor=True))
    assert c.get("/api/status").json()["mode"] == "floor"
    evs = sse(c.post("/api/chat", json={"text": "hi"}).text)
    assert [e["kind"] for e in evs] == ["turn", "interject", "turn"]
    assert c.post("/api/floor/mute", json={"persona": "root"}).json()["muted"] == {"root": 30}
    assert c.post("/api/floor/mute", json={"persona": "bob"}).status_code == 400
    assert c.post("/api/floor/delete", json={}).status_code == 404


# ---------------------------------------------------------------------- demo
def test_demo_interrupt_build_hand_and_switches(tmp_path):
    c, up = client(tmp_path, demo=True)
    st = c.get("/api/status").json()
    assert st["mode"] == "demo" and st["floor"]["state"]["profile"] == "home_text"
    evs = sse(c.post("/api/chat", json={"text": "Should I get the A2000 now?"}).text)
    k = [e["kind"] for e in evs]
    assert "interject" in k and "yield" in k and k[-1] == "turn"
    inter = next(e for e in evs if e["kind"] == "interject")
    assert inter["speaker"] == "root" and inter["pressure"] == 0.74 and inter["voice"] == "root-v1"
    assert len(inter["data"]["dropped"]) == 1

    evs = sse(c.post("/api/chat", json={"text": "How should I structure the paper?"}).text)
    assert [e["speaker"] for e in evs if e["kind"] == "build"] == ["root", "finch"]

    # switch interrupt off: the act is held and shown as a comment instead
    c.post("/api/floor/config", json={"disable": ["interrupt"]})
    evs = sse(c.post("/api/chat", json={"text": "Buy the GPU?"}).text)
    k = [e["kind"] for e in evs]
    assert "interject" not in k and "suppressed" in k and "comment" in k

    st = c.post("/api/floor/config", json={"profile": "focus"}).json()
    assert st["profile"] == "focus"
    sse(c.post("/api/chat", json={"text": "Should I take the job offer?"}).text)
    assert c.get("/api/floor/state").json()["pending_hand"]["persona"] == "root"
    c.post("/api/floor/grant", json={})
    assert c.get("/api/floor/state").json()["pending_hand"] is None

    assert c.post("/api/floor/mute", json={"persona": "root", "minutes": 5}).json()["muted"]
    assert (
        c.post("/api/floor/config", json={"enable": ["duet"], "profile": "home_text"}).status_code
        == 400
    )
    assert not [q for q in up.seen]  # demo never calls upstream


def test_hub_dedupes_by_id_and_time():
    hub = Hub()
    ev = make_event("say", "finch", "x")
    hub.publish(ev)
    hub.publish(dict(ev))
    assert len(hub.recent()) == 1
    hub.publish({**ev, "t": ev["t"] + 100})  # same id after an OpenJarvis restart
    assert len(hub.recent()) == 2


@pytest.mark.parametrize("text", ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>"])
def test_text_is_passed_as_data_not_markup(tmp_path, text):
    c, _ = client(tmp_path, demo=True)
    evs = sse(c.post("/api/chat", json={"text": text}).text)
    assert evs[0]["text"] == text  # JSON string; the frontend renders it as text
