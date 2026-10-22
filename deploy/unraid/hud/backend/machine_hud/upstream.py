"""Talking to OpenJarvis and Cognee. Every key is added here, server side.

Two chat paths, one event shape out:

- floor: the persona package is loaded in OpenJarvis
  (``/api/custom/floor``); its events are already in the HUD shape.
- plain: one persona over ``/v1/chat/completions`` (stream). The named SSE
  events of upstream ``stream_bridge`` become TOOL lines and persona states;
  the answer text is cut into sentences as it streams.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .events import make_event
from .settings import Settings

FLOOR = "/api/custom/floor"
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")


async def sse_lines(resp: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Yield (event name, data) pairs from a server-sent event stream."""
    name, data = "", []
    async for line in resp.aiter_lines():
        if line == "":
            if data:
                yield name, "\n".join(data)
            name, data = "", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield name, "\n".join(data)


async def _empty() -> AsyncIterator[tuple[str, str]]:
    return
    yield  # pragma: no cover


class Upstream:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        headers = (
            {"Authorization": f"Bearer {settings.openjarvis_key}"}
            if settings.openjarvis_key
            else {}
        )
        self.oj = httpx.AsyncClient(
            base_url=settings.openjarvis_url,
            headers=headers,
            transport=transport,
            timeout=httpx.Timeout(10.0, read=300.0),
        )
        cheaders = {"X-Api-Key": settings.cognee_key} if settings.cognee_key else {}
        self.cg = httpx.AsyncClient(
            base_url=settings.cognee_url, headers=cheaders, transport=transport, timeout=5.0
        )

    async def close(self) -> None:
        await self.oj.aclose()
        await self.cg.aclose()

    # ------------------------------------------------------------- status
    async def _probe(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            r = await client.get(path, timeout=3.0)
            ok = r.status_code < 400
            out: dict[str, Any] = {"ok": ok, "code": r.status_code}
            if ok and "json" in r.headers.get("content-type", ""):
                out["body"] = r.json()
        except httpx.HTTPError as exc:
            out = {"ok": False, "error": type(exc).__name__}
        out["ms"] = round((time.perf_counter() - t0) * 1000)
        return out

    async def status(self) -> dict[str, Any]:
        oj = await self._probe(self.oj, "/health")
        info = await self._probe(self.oj, "/v1/info")
        floor = await self._probe(self.oj, f"{FLOOR}/state")
        cg = await self._probe(self.cg, "/health")
        oj.pop("body", None)
        cg.pop("body", None)
        return {
            "openjarvis": oj,
            "cognee": cg,
            "floor": {"ok": floor["ok"], "state": floor.get("body")},
            "info": info.get("body") or {},
        }

    async def floor_ready(self) -> bool:
        try:
            r = await self.oj.get(f"{FLOOR}/state", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # -------------------------------------------------------------- floor
    async def floor_state(self) -> dict[str, Any]:
        r = await self.oj.get(f"{FLOOR}/state")
        r.raise_for_status()
        return r.json()

    async def floor_post(self, action: str, body: dict[str, Any]) -> httpx.Response:
        return await self.oj.post(f"{FLOOR}/{action}", json=body)

    async def floor_chat(self, text: str) -> AsyncIterator[dict[str, Any]]:
        async with self.oj.stream("POST", f"{FLOOR}/chat", json={"text": text}) as resp:
            resp.raise_for_status()
            async for _, data in sse_lines(resp):
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    async def floor_feed(self) -> AsyncIterator[dict[str, Any]]:
        timeout = httpx.Timeout(10.0, read=None)
        async with self.oj.stream("GET", f"{FLOOR}/events", timeout=timeout) as resp:
            resp.raise_for_status()
            async for _, data in sse_lines(resp):
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    # ------------------------------------------------- relays to custom code
    async def oj_json(self, method: str, path: str, body: Any = None) -> httpx.Response:
        return await self.oj.request(
            method, path, json=body, timeout=httpx.Timeout(10.0, read=300.0)
        )

    async def oj_sse(self, path: str, body: Any) -> AsyncIterator[dict[str, Any]]:
        async with self.oj.stream("POST", path, json=body) as resp:
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode(errors="replace")[:300]
                raise httpx.HTTPStatusError(detail, request=resp.request, response=resp)
            async for _, data in sse_lines(resp):
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    async def oj_upload(
        self, path: str, stream: AsyncIterator[bytes], content_type: str
    ) -> httpx.Response:
        return await self.oj.post(
            path,
            content=stream,
            headers={"Content-Type": content_type},
            timeout=httpx.Timeout(30.0, read=600.0, write=600.0),
        )

    # -------------------------------------------------------------- plain
    async def plain_chat(
        self, text: str, history: list[dict[str, str]], turn: int
    ) -> AsyncIterator[dict[str, Any]]:
        who = self.s.plain_persona
        t0 = time.time()
        yield make_event("turn", text=text, turn=turn, data={"phase": "start", "mode": "plain"})
        yield make_event("state", who, turn=turn, data={"state": "THINKING"})
        body = {
            "model": "",
            "stream": True,
            "messages": [*history[-12:], {"role": "user", "content": text}],
        }
        info = await self._probe(self.oj, "/v1/info")
        body["model"] = (info.get("body") or {}).get("model", "")
        buf, speaking, tools = "", False, {}
        try:
            async with self.oj.stream("POST", "/v1/chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode(errors="replace")[:300]
                    yield make_event(
                        "error", text=f"OpenJarvis {resp.status_code}: {detail}", turn=turn
                    )
                    resp_lines = None
                else:
                    resp_lines = sse_lines(resp)
                async for name, data in resp_lines or _empty():
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if name == "tool_call_start":
                        tool = payload.get("tool", "tool")
                        tools[tool] = time.time()
                        yield make_event(
                            "tool", who, tool, turn=turn, data={"phase": "start", "tool": tool}
                        )
                        continue
                    if name == "tool_call_end":
                        tool = payload.get("tool", "tool")
                        latency = payload.get("latency")
                        if latency is None and tool in tools:
                            latency = time.time() - tools.pop(tool)
                        yield make_event(
                            "tool",
                            who,
                            tool,
                            turn=turn,
                            data={
                                "phase": "end",
                                "tool": tool,
                                "ok": payload.get("success", True),
                                "latency": round(float(latency or 0), 2),
                            },
                        )
                        continue
                    if name:
                        continue  # agent_turn_start, inference_*: state is already THINKING
                    for choice in payload.get("choices", []):
                        delta = (choice.get("delta") or {}).get("content") or ""
                        if not delta:
                            continue
                        if not speaking:
                            speaking = True
                            yield make_event("state", who, turn=turn, data={"state": "SPEAKING"})
                        buf += delta
                        parts = SENTENCE_END.split(buf)
                        for sentence in parts[:-1]:
                            yield make_event(
                                "say", who, sentence.strip(), turn=turn, voice=f"{who}-v1"
                            )
                        buf = parts[-1]
        except httpx.HTTPError as exc:
            yield make_event(
                "error", text=f"OpenJarvis unreachable: {type(exc).__name__}", turn=turn
            )
        if buf.strip():
            yield make_event("say", who, buf.strip(), turn=turn, voice=f"{who}-v1")
        yield make_event("state", who, turn=turn, data={"state": "LISTENING"})
        yield make_event(
            "turn",
            turn=turn,
            data={
                "phase": "end",
                "mode": "plain",
                "seconds": round(time.time() - t0, 1),
            },
        )
