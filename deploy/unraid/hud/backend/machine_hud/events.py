"""HUD events: one shape for every source, fanned out to the browser.

The shape is the floor package's (``home-jarvis`` branch, ``events.py``):
``{"id", "ts", "t", "kind", "speaker", "text", "turn", "pressure", "source",
"voice", "data"}``. The plain chat path and the demo produce the same shape,
so the frontend has one reader.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Any

_ids = itertools.count(1)


def make_event(kind: str, speaker: str = "", text: str = "", **kw: Any) -> dict[str, Any]:
    now = time.time()
    ev: dict[str, Any] = {
        "id": f"hud_{next(_ids):06d}",
        "t": now,
        "ts": time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}",
        "kind": kind,
    }
    if speaker:
        ev["speaker"] = speaker
    if text:
        ev["text"] = text
    for k, v in kw.items():
        if v not in (None, "", {}):
            ev[k] = v
    return ev


class Hub:
    """Ring buffer plus one asyncio queue per connected browser."""

    def __init__(self, size: int = 500) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=size)
        self._seen: deque[str] = deque(maxlen=size * 2)
        self._queues: set[asyncio.Queue] = set()

    def publish(self, ev: dict[str, Any]) -> None:
        # The same floor event can arrive from the chat stream and the feed.
        # Ids restart when OpenJarvis restarts, so the time is part of the key.
        eid = f"{ev.get('id', '')}@{ev.get('t', '')}" if ev.get("id") else ""
        if eid and eid in self._seen:
            return
        if eid:
            self._seen.append(eid)
        self._buf.append(ev)
        for q in list(self._queues):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # a slow browser drops events rather than stalling the turn

    def recent(self, n: int = 200) -> list[dict[str, Any]]:
        return list(self._buf)[-n:]

    def attach(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues.add(q)
        return q

    def detach(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    @property
    def listeners(self) -> int:
        return len(self._queues)
