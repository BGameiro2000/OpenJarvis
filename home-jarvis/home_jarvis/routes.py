"""HTTP routes for the floor manager, mounted on the OpenJarvis server.

All under ``/api/custom/floor`` (behind the OpenJarvis API key):

    POST /chat       {"text": "..."}  -> text/event-stream of this turn's events
    GET  /events     live text/event-stream of every floor event (?replay=N)
    GET  /state      limits, profile, mute, fatigue, pending hand
    POST /config     {"profile"?, "enable"?, "disable"?, "threshold"?, "who"?}
    POST /mute       {"persona": "root", "minutes": 30}
    POST /unmute     {"persona": "root"}
    POST /grant      grant the floor to a raised hand
    POST /feedback   {"persona": "root", "accepted": true}

Each SSE message is ``data: <FloorEvent JSON>``; the HUD maps them to screen
changes one to one.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

from pydantic import BaseModel

from .config import ConfigError
from .events import FloorEvent
from .floor import FloorManager

PREFIX = "/api/custom/floor"


# Module level: FastAPI cannot resolve request models defined inside a
# function when annotations are postponed.
class ChatIn(BaseModel):
    text: str


class ConfigIn(BaseModel):
    profile: str | None = None
    enable: list[str] = []
    disable: list[str] = []
    threshold: float | None = None
    who: str = "hud"


class PersonaIn(BaseModel):
    persona: str
    minutes: float = 30
    accepted: bool = True


def _sse(event: FloorEvent) -> str:
    return f"data: {json.dumps(event.to_json(), ensure_ascii=False)}\n\n"


def build_router(floor: FloorManager) -> Any:
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import StreamingResponse

    router = APIRouter()

    def _persona(name: str) -> str:
        name = name.lower()
        if name not in ("finch", "root"):
            raise HTTPException(400, "persona must be finch or root")
        return name

    @router.post(f"{PREFIX}/chat")
    async def chat(body: ChatIn):
        if not body.text.strip():
            raise HTTPException(400, "empty text")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        def listener(ev: FloorEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        floor.log.subscribe(listener)

        def work() -> None:
            try:
                floor.run_turn(body.text)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, done)

        task = loop.run_in_executor(None, work)

        async def stream():
            try:
                while True:
                    item = await queue.get()
                    if item is done:
                        break
                    yield _sse(item)
            finally:
                floor.log.unsubscribe(listener)
                await task

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get(f"{PREFIX}/events")
    async def events(replay: int = 50):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

        def listener(ev: FloorEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        backlog = floor.log.recent(max(0, min(replay, 500)))
        floor.log.subscribe(listener)

        async def stream():
            try:
                for ev in backlog:
                    yield _sse(ev)
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                        yield _sse(ev)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                floor.log.unsubscribe(listener)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get(f"{PREFIX}/state")
    def state():
        return floor.snapshot()

    @router.post(f"{PREFIX}/config")
    def config(body: ConfigIn):
        try:
            cfg = floor.config
            if body.profile:
                cfg = cfg.with_profile(body.profile)
            if body.enable or body.disable:
                cfg = cfg.with_modes(body.enable, body.disable)
            if body.threshold is not None:
                if not 0 < body.threshold <= 1:
                    raise ConfigError("threshold must be in (0, 1]")
                cfg = replace(cfg, threshold=body.threshold)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        how = ", ".join(
            x
            for x in (
                f"profile {body.profile}" if body.profile else "",
                f"on {','.join(body.enable)}" if body.enable else "",
                f"off {','.join(body.disable)}" if body.disable else "",
                f"threshold {body.threshold}" if body.threshold is not None else "",
            )
            if x
        )
        floor.set_config(cfg, who=body.who, how=how)
        return floor.snapshot()

    @router.post(f"{PREFIX}/mute")
    def mute(body: PersonaIn):
        floor.mute(_persona(body.persona), body.minutes)
        return floor.snapshot()

    @router.post(f"{PREFIX}/unmute")
    def unmute(body: PersonaIn):
        floor.unmute(_persona(body.persona))
        return floor.snapshot()

    @router.post(f"{PREFIX}/grant")
    def grant():
        return [e.to_json() for e in floor.grant()]

    @router.post(f"{PREFIX}/feedback")
    def feedback(body: PersonaIn):
        floor.feedback(_persona(body.persona), body.accepted)
        return floor.snapshot()

    return router
