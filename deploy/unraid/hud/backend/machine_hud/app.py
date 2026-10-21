"""Machine HUD backend for frontend.

The browser talks only to this app. It adds the OpenJarvis and Cognee keys
server side, turns every source into one event shape, and serves the built
SPA. Nothing here writes to the vault.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .demo import DemoFloor, stream_until_end
from .events import Hub
from .security import SecurityMiddleware
from .settings import Settings
from .upstream import Upstream

logger = logging.getLogger("machine_hud")

FLOOR_ACTIONS = {"config", "mute", "unmute", "grant", "feedback"}
PERSONAS = {"finch", "root"}


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class FloorIn(BaseModel):
    profile: str | None = None
    enable: list[str] = []
    disable: list[str] = []
    persona: str | None = None
    minutes: float = 30
    accepted: bool = True


def _sse(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, separators=(',', ':'))}\n\n"


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    demo_pace: float = 0.45,
) -> FastAPI:
    s = settings or Settings.from_env()
    hub = Hub()
    up = Upstream(s, transport)
    demo = DemoFloor(hub.publish, pace=demo_pace) if s.demo else None
    runtime: dict[str, Any] = {"floor": False, "history": [], "turn": 0, "relay": None}

    async def relay() -> None:
        """Follow the floor package's event feed while it is there."""
        while True:
            try:
                if await up.floor_ready():
                    runtime["floor"] = True
                    async for ev in up.floor_feed():
                        hub.publish(ev)
                runtime["floor"] = False
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.debug("floor relay: %s", exc)
                runtime["floor"] = False
            await asyncio.sleep(5)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if s.auth == "off":
            logger.warning("HUD_AUTH is off: anyone on the network can use the HUD")
        task = None if s.demo else asyncio.create_task(relay())
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await up.close()

    app = FastAPI(
        title="Machine HUD",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SecurityMiddleware, settings=s)
    app.state.hub = hub
    app.state.runtime = runtime

    def mode() -> str:
        return "demo" if s.demo else ("floor" if runtime["floor"] else "plain")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        base = {
            "version": __version__,
            "owner": s.owner,
            "now": time.time(),
            "tz": time.strftime("%Z"),
            "identity": getattr(request.state, "identity", ""),
            "auth": s.auth,
            "cameras": "off",  # camera phase not built: always off, shown in the top bar
            "link": "tailscale" if request.headers.get("tailscale-user-login") else "lan",
            "listeners": hub.listeners,
        }
        if s.demo:
            return {
                **base,
                "mode": "demo",
                "engine": "demo",
                "model": "scripted",
                "services": {
                    "openjarvis": {"ok": True, "demo": True},
                    "cognee": {"ok": True, "demo": True},
                },
                "floor": {"ok": True, "state": demo.snapshot()},
            }
        st = await up.status()
        runtime["floor"] = st["floor"]["ok"]
        return {
            **base,
            "mode": mode(),
            "engine": st["info"].get("engine", ""),
            "model": st["info"].get("model", ""),
            "services": {"openjarvis": st["openjarvis"], "cognee": st["cognee"]},
            "floor": st["floor"],
        }

    @app.get("/api/events")
    async def events(request: Request, replay: int = 200) -> StreamingResponse:
        q = hub.attach()
        backlog = hub.recent(max(0, min(replay, 500)))

        async def gen() -> AsyncIterator[str]:
            try:
                yield ": hello\n\n"
                for ev in backlog:
                    yield _sse(ev)
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield _sse(ev)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                hub.detach(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat")
    async def chat(body: ChatIn) -> StreamingResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty message")

        async def gen() -> AsyncIterator[str]:
            m = mode()
            if m == "demo":
                q = hub.attach()
                # The turn runs on even if this browser goes away mid turn.
                task = asyncio.create_task(demo.run_turn(text))
                runtime.setdefault("tasks", set()).add(task)
                task.add_done_callback(runtime["tasks"].discard)
                try:
                    async for ev in stream_until_end(q):
                        yield _sse(ev)
                finally:
                    hub.detach(q)
                return
            if m == "floor":
                try:
                    async for ev in up.floor_chat(text):
                        hub.publish(ev)
                        yield _sse(ev)
                    return
                except httpx.HTTPError as exc:
                    logger.warning("floor chat failed, falling back to plain: %s", exc)
                    runtime["floor"] = False
            runtime["turn"] += 1
            reply: list[str] = []
            async for ev in up.plain_chat(text, runtime["history"], runtime["turn"]):
                if ev["kind"] == "say":
                    reply.append(ev["text"])
                hub.publish(ev)
                yield _sse(ev)
            runtime["history"] += [
                {"role": "user", "content": text},
                {"role": "assistant", "content": " ".join(reply)},
            ]
            runtime["history"] = runtime["history"][-24:]

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/floor/state")
    async def floor_state() -> dict[str, Any]:
        if s.demo:
            return demo.snapshot()
        try:
            return await up.floor_state()
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"floor not loaded in OpenJarvis ({type(exc).__name__})")

    @app.post("/api/floor/{action}")
    async def floor_action(action: str, body: FloorIn) -> Any:
        if action not in FLOOR_ACTIONS:
            raise HTTPException(404, "unknown action")
        if body.persona is not None and body.persona not in PERSONAS:
            raise HTTPException(400, "persona must be finch or root")
        if s.demo:
            try:
                if action == "config":
                    return demo.set_config(body.profile, body.enable, body.disable)
                if action == "mute":
                    return demo.mute(body.persona or "root", body.minutes)
                if action == "unmute":
                    return demo.unmute(body.persona or "root")
                if action == "grant":
                    return await demo.grant()
                return demo.snapshot()
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        payload = body.model_dump(exclude_none=True)
        try:
            r = await up.floor_post(action, payload)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"OpenJarvis unreachable ({type(exc).__name__})")
        try:
            data = r.json()
        except ValueError:
            data = {"detail": r.text[:300]}
        return JSONResponse(data, status_code=r.status_code)

    # ------------------------------------------------------------- the SPA
    index = s.static_dir / "index.html"
    if (s.static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=s.static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Any:
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (s.static_dir / path).resolve()
        if path and candidate.is_file() and s.static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "frontend not built"}, status_code=503)

    return app


def main() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("HUD_PORT", "8080")),
        log_level="info",
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
