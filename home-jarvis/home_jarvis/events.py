"""Floor events: one record per visible change, in the HUD guide's shape.

Every decision the floor manager makes becomes one ``FloorEvent``. The HUD
renders these and nothing else, so it never has to guess state from text.

Kinds (HUD guide section 4, plus the ones the interaction modes add):

    state      persona state change (IDLE, LISTENING, THINKING, SPEAKING, INTERJECT)
    bid        who takes the floor first, with both scores
    say        a sentence spoken by the floor holder
    interject  the listener cuts in at a sentence boundary
    cut        hard cut (safety allowlist), speaker stopped mid-reply
    yield      the interrupted persona's closing line (or none)
    add        turn-end addendum from the listener
    build      the listener builds on the reply (extends it)
    comment    silent aside: shown, never spoken
    correct    factual correction after the turn
    hand       the listener raised a hand and waits for permission
    suppressed an act that would have fired but was blocked (for tuning)
    config     strategy, profile or mute change
    turn       turn start or end
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

_ids = itertools.count(1)


@dataclass
class FloorEvent:
    kind: str
    speaker: str = ""
    text: str = ""
    turn: int = 0
    reply_to: str = ""
    pressure: float | None = None
    source: str = ""
    voice: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    ts: str = ""
    t: float = 0.0

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        return {k: v for k, v in out.items() if v not in ("", None, {})}


class EventLog:
    """Ring buffer of floor events plus fan-out to listeners.

    ``emit`` is called from the turn thread; listeners must only enqueue.
    ``publish`` (optional) forwards each event to the OpenJarvis bus.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        size: int = 2000,
        publish: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self._clock = clock
        self._buf: deque[FloorEvent] = deque(maxlen=size)
        self._listeners: list[Callable[[FloorEvent], None]] = []
        self._lock = threading.Lock()
        self._publish = publish

    def emit(self, event: FloorEvent) -> FloorEvent:
        now = self._clock()
        event.t = now
        event.ts = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"
        event.id = event.id or f"ev_{next(_ids):06d}"
        with self._lock:
            self._buf.append(event)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001  (an observer never breaks a turn)
                pass
        if self._publish is not None:
            try:
                prefix = "persona" if event.speaker else "floor"
                self._publish(f"{prefix}.{event.kind}", event.to_json())
            except Exception:  # noqa: BLE001
                pass
        return event

    def subscribe(self, listener: Callable[[FloorEvent], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[FloorEvent], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def recent(self, n: int = 200) -> list[FloorEvent]:
        with self._lock:
            return list(self._buf)[-n:]
