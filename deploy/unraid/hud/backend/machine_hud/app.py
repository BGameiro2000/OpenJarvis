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
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import council as council_mod
from .council import DemoCouncil
from .demo import DemoFloor, stream_until_end
from .events import Hub, make_event
from .intake import DemoIntake, list_inbox, read_log, summarize
from .memory import CogneeGraph, Memory
from .security import SecurityMiddleware
from .settings import Settings
from .upstream import Upstream
from .vault import VaultIndex

logger = logging.getLogger("machine_hud")

FLOOR_ACTIONS = {"config", "mute", "unmute", "grant", "feedback"}
INTAKE = "/api/custom/intake"
COUNCIL = "/api/custom/council"
UPLOAD_CAP = 200 * 1024 * 1024
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


class ScanIn(BaseModel):
    apply: bool = False


class DecideIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    action: str
    envelope: dict[str, Any] | None = None
    list: dict[str, Any] | None = None


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    why: str = ""


class QuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def _detail(detail: Any) -> str:
    """The sorter's refusal as one readable line (it may send the failed checks)."""
    if isinstance(detail, dict):
        failed = [c.get("name", "") for c in detail.get("checks") or [] if not c.get("ok")]
        msg = str(detail.get("error") or "refused")
        return msg + (f" (failed: {', '.join(failed)})" if failed else "")
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail)


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
    vault = VaultIndex(s.vault_root)
    memory = Memory(vault, None if s.demo else CogneeGraph(up.cg, s.cognee_dataset), demo=s.demo)

    def intake_event(kind: str, item: dict[str, Any], applied: bool) -> None:
        env = item.get("envelope") or {}
        if kind == "file":
            hub.publish(
                make_event("file", text=item.get("path", ""), data={"source": item.get("source")})
            )
            return
        if kind == "route":
            text = f"{env.get('route')} · {env.get('confidence', 0):.2f} · {env.get('target_list')}"
        else:
            text = item.get("reason") or env.get("why", "")
        hub.publish(
            make_event(
                kind,
                text=text,
                data={
                    "source": item.get("source"),
                    "route": env.get("route"),
                    "id": item.get("id"),
                    "target_list": env.get("target_list"),
                    "confidence": env.get("confidence"),
                    "items": item.get("count", 1),
                    "dry_run": not applied,
                },
            )
        )

    demo_intake = DemoIntake(intake_event) if s.demo else None
    demo_council = DemoCouncil(hub.publish, s.vault_root, pace=demo_pace) if s.demo else None
    if demo:
        demo.council = demo_council
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

    # -------------------------------------------------------------- memory
    @app.get("/api/memory/search")
    async def memory_search(
        q: str,
        mode: str = "hybrid",
        type: str = "",
        date_from: str = "",
        date_to: str = "",
        exclude: str = "",
        limit: int = 25,
    ) -> dict[str, Any]:
        if mode not in ("hybrid", "vault", "graph"):
            raise HTTPException(400, "mode must be hybrid, vault or graph")
        types = {t.strip().lower() for t in type.split(",") if t.strip()} or None
        ex = [e.strip() for e in exclude.split(",") if e.strip()]
        return await memory.search(
            q[:300], mode, types, date_from[:10], date_to[:10], ex, max(1, min(limit, 50))
        )

    @app.get("/api/memory/note")
    async def memory_note(path: str) -> dict[str, Any]:
        note = await asyncio.to_thread(vault.note, path)
        if note is None:
            raise HTTPException(404, "no such note in the vault")
        note["obsidian"] = "obsidian://open?vault=TheMachine&file=" + quote(note["path"], safe="")
        return note

    @app.get("/api/memory/stats")
    async def memory_stats() -> dict[str, Any]:
        await asyncio.to_thread(vault.refresh)
        return vault.stats()

    @app.get("/api/graph")
    async def graph(path: str = "", node: str = "", q: str = "") -> dict[str, Any]:
        await asyncio.to_thread(vault.refresh)
        return await memory.graph(path=path, node=node, q=q[:200])

    # -------------------------------------------------------------- intake
    async def sorter_call(method: str, path: str, body: Any = None) -> Any:
        try:
            r = await up.oj_json(method, path, body)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"OpenJarvis unreachable ({type(exc).__name__})")
        if r.status_code == 404:
            raise HTTPException(503, "sorter not loaded in OpenJarvis (home_jarvis package)")
        try:
            data = r.json()
        except ValueError:
            data = {"detail": r.text[:300]}
        if r.status_code >= 400:
            raise HTTPException(r.status_code, _detail(data.get("detail", "sorter error")))
        return data

    @app.get("/api/intake")
    async def intake_board() -> dict[str, Any]:
        if s.demo:
            return demo_intake.board()
        inbox = await asyncio.to_thread(list_inbox, s.vault_root)
        log = await asyncio.to_thread(read_log, s.vault_root)
        sorter, reason = None, ""
        try:
            sorter = await sorter_call("GET", f"{INTAKE}/state")
        except HTTPException as exc:
            reason = str(exc.detail)
        pending = len(sorter.get("pending", [])) if sorter else None
        return {
            "inbox": inbox,
            "log": log,
            "sorter": sorter,
            "sorter_reason": reason,
            "summary": summarize(inbox, log, pending),
        }

    @app.post("/api/intake/scan")
    async def intake_scan(body: ScanIn) -> Any:
        if s.demo:
            return demo_intake.scan(body.apply)
        return await sorter_call("POST", f"{INTAKE}/scan", body.model_dump())

    @app.post("/api/intake/decide")
    async def intake_decide(body: DecideIn) -> Any:
        if body.action not in ("file", "add_list", "edit", "leave"):
            raise HTTPException(400, "action must be file, add_list, edit or leave")
        if s.demo:
            try:
                return demo_intake.decide(body.id, body.action, body.envelope, body.list)
            except KeyError as exc:
                raise HTTPException(404, str(exc))
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        return await sorter_call("POST", f"{INTAKE}/decide", body.model_dump(exclude_none=True))

    @app.post("/api/intake/note")
    async def intake_note(body: CaptureIn) -> Any:
        if s.demo:
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            return demo_intake.capture(f"{stamp}-capture.md", body.text.encode())
        return await sorter_call("POST", f"{INTAKE}/capture", body.model_dump())

    @app.post("/api/intake/upload")
    async def intake_upload(request: Request) -> Any:
        ctype = request.headers.get("content-type", "")
        if not ctype.startswith("multipart/form-data"):
            raise HTTPException(400, "send the file as multipart/form-data")
        size = int(request.headers.get("content-length") or 0)
        if size > UPLOAD_CAP:
            raise HTTPException(413, "file too large (200 MB max)")
        if s.demo:
            form = await request.form()
            saved = []
            for _, f in form.multi_items():
                if hasattr(f, "read"):
                    data = await f.read()
                    saved += demo_intake.capture(f.filename or "upload", data)["saved"]
            return {"saved": saved}
        seen = 0

        async def body_stream():
            nonlocal seen
            async for chunk in request.stream():
                seen += len(chunk)
                if seen > UPLOAD_CAP:
                    raise HTTPException(413, "file too large (200 MB max)")
                yield chunk

        try:
            r = await up.oj_upload(f"{INTAKE}/capture", body_stream(), ctype)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"OpenJarvis unreachable ({type(exc).__name__})")
        if r.status_code == 404:
            raise HTTPException(503, "sorter not loaded in OpenJarvis (home_jarvis package)")
        return JSONResponse(
            r.json() if "json" in r.headers.get("content-type", "") else {"detail": r.text[:300]},
            status_code=r.status_code,
        )

    # ------------------------------------------------------------- council
    @app.get("/api/council")
    async def council_list() -> dict[str, Any]:
        if s.demo:
            return {"sims": demo_council.list(), "gods": council_mod.GODS}
        sims = await asyncio.to_thread(council_mod.list_sims, s.vault_root)
        return {"sims": sims, "gods": council_mod.GODS}

    @app.get("/api/council/{sim}")
    async def council_get(sim: str) -> dict[str, Any]:
        rec = (
            demo_council.get(sim)
            if s.demo
            else await asyncio.to_thread(council_mod.get_sim, s.vault_root, sim)
        )
        if rec is None:
            raise HTTPException(404, "no such simulation")
        return rec

    @app.post("/api/council/run")
    async def council_run(body: QuestionIn) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            if s.demo:
                q = hub.attach()
                task = asyncio.create_task(demo_council.run(body.question))
                runtime.setdefault("tasks", set()).add(task)
                task.add_done_callback(runtime["tasks"].discard)
                try:
                    while True:
                        ev = await q.get()
                        yield _sse(ev)
                        if ev.get("kind") == "council_saved":
                            return
                finally:
                    hub.detach(q)
            try:
                async for ev in up.oj_sse(f"{COUNCIL}/run", body.model_dump()):
                    hub.publish(ev)
                    yield _sse(ev)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                msg = (
                    "council not loaded in OpenJarvis (home_jarvis package)"
                    if code == 404
                    else f"council failed ({code})"
                )
                yield _sse(make_event("error", text=msg))
            except httpx.HTTPError as exc:
                yield _sse(
                    make_event("error", text=f"OpenJarvis unreachable ({type(exc).__name__})")
                )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

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
