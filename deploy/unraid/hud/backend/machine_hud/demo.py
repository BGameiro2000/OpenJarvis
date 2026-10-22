"""Demo floor: scripted Finch and Root, in the floor package's event shape.

No upstream, no key, no cost. One scenario per interaction mode, picked by
keywords, paced so the HUD's states and motion can be seen. The live switches
(profile, modes, mute, grant) work on an in-memory copy of the floor state.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .events import make_event

MODES = (
    "bidding",
    "interrupt",
    "hard_cut",
    "build",
    "addendum",
    "correct",
    "hand",
    "comment",
    "duet",
)
PROFILES = {
    "home_text": ["bidding", "interrupt", "build", "addendum", "comment", "correct"],
    "home_voice": ["bidding", "interrupt", "hard_cut", "addendum", "comment"],
    "duet": ["duet", "hard_cut"],
    "focus": ["bidding", "hand", "comment", "hard_cut"],
    "night": ["comment", "hard_cut"],
}
ACT_MODE = {
    "interject": "interrupt",
    "cut": "hard_cut",
    "build": "build",
    "add": "addendum",
    "correct": "correct",
    "hand": "hand",
    "comment": "comment",
}


@dataclass
class Scenario:
    keys: tuple[str, ...]
    speaker: str
    reply: list[str]
    act: str = ""
    at: int = 0
    pressure: float = 0.0
    source: str = ""
    reason: str = ""
    text: str = ""
    exchange: list[str] = field(default_factory=list)


SCENARIOS = [
    Scenario(
        ("smoke", "stove", "gas"),
        "root",
        ["Checking the kitchen sensor now."],
        "cut",
        -1,
        1.0,
        "safety",
        "safety word",
        "Stop. Check the stove first, then we talk.",
    ),
    Scenario(
        ("a2000", "gpu", "ram", "buy"),
        "finch",
        [
            "I put it to the council.",
            "Tyche found two used listings under your ceiling.",
            "Daedalus says the RAM upgrade unblocks more of the current plan than the GPU does.",
            "If you want both, the A2000 listing at 310 euros is the better of the two.",
        ],
        "interject",
        2,
        0.74,
        "council:ate",
        "late night research loop, impulse window",
        "Interjecting. Ate flagged a late night research loop, three hardware tabs since "
        "02:00. Buy nothing tonight.",
    ),
    Scenario(
        ("paper", "structure", "draft"),
        "finch",
        [
            "Lead with the cross section you are most sure of, then the systematic budget.",
            "Keep the comparison with previous measurements for the end.",
        ],
        "build",
        0,
        0.55,
        "calendar",
        "a concrete next step was missing",
        "And block two mornings this week for the systematics table, before the text.",
        ["Good. Then draft the table first on Tuesday; the text follows the table."],
    ),
    Scenario(
        ("remind", "call", "tomorrow"),
        "root",
        [
            "Done. Reminder set for tomorrow at nine, and the call link is in the note.",
        ],
        "add",
        0,
        0.45,
        "memory",
        "agenda lives in a note",
        "The agenda from last time is in projects/ntof-meeting.md, if you want it open.",
    ),
    Scenario(
        ("deadline", "due", "when"),
        "finch",
        [
            "The review is due on the 30th of November, so you have five weeks.",
        ],
        "correct",
        0,
        0.8,
        "memory",
        "the date moved in a later note",
        "Correction: it moved to the 7th of December, per the email you filed on Monday.",
    ),
    Scenario(
        ("weather", "rain", "walk"),
        "root",
        [
            "Dry until four, then rain. A walk now works.",
        ],
        "comment",
        0,
        0.3,
        "context",
        "aside, not worth speaking",
        "You walked less than usual this week.",
    ),
    Scenario(
        ("job", "offer", "career"),
        "finch",
        [
            "The offer is stronger on research time and weaker on salary.",
            "I would ask for the salary review clause in writing before deciding.",
        ],
        "hand",
        0,
        0.6,
        "council:hermes",
        "can wait for permission",
        "Hermes noted the notice period is three months. Worth asking now, not later.",
    ),
]
DEFAULT = Scenario(
    (),
    "finch",
    ["Noted.", "I checked the notes and there is nothing that changes the plan."],
    "comment",
    0,
    0.2,
    "context",
    "nothing to add out loud",
    "Nothing from me on this one.",
)


class DemoFloor:
    def __init__(self, publish: Callable[[dict[str, Any]], None], pace: float = 0.45) -> None:
        self.publish = publish
        self.pace = pace
        self.profile = "home_text"
        self.enabled = list(PROFILES["home_text"])
        self.threshold = 0.70
        self.turn = 0
        self.muted: dict[str, float] = {}
        self.pending_hand: dict[str, Any] | None = None
        self.spoken_today = 0
        self.holder = "finch"
        self.council = None  # DemoCouncil, set by the app: consulted on the GPU question
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------- state
    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "profile": self.profile,
            "enabled": list(self.enabled),
            "threshold": self.threshold,
            "holder": self.holder,
            "turn": self.turn,
            "cooldown_left_s": 0.0,
            "muted": {p: round((t - now) / 60, 1) for p, t in self.muted.items() if t > now},
            "fatigue": {"finch": 0, "root": 0},
            "spoken_today": self.spoken_today,
            "daily_cap": 15,
            "pending_hand": self.pending_hand,
            "profiles": sorted(PROFILES),
            "demo": True,
        }

    def _emit(self, kind: str, speaker: str = "", text: str = "", **kw: Any) -> dict[str, Any]:
        voice = f"{speaker}-v1" if speaker and kind != "state" else ""
        ev = make_event(kind, speaker, text, turn=self.turn, voice=voice, **kw)
        self.publish(ev)
        return ev

    def _state(self, persona: str, state: str) -> None:
        self._emit("state", persona, data={"state": state})

    def set_config(self, profile: str | None, enable: list[str], disable: list[str]) -> dict:
        if profile:
            if profile not in PROFILES:
                raise ValueError(f"unknown profile {profile!r}")
            self.profile, self.enabled = profile, list(PROFILES[profile])
        for m in disable:
            if m in self.enabled:
                self.enabled.remove(m)
        for m in enable:
            if m not in MODES:
                raise ValueError(f"unknown mode {m!r}")
            if m not in self.enabled:
                self.enabled.append(m)
        if "duet" in self.enabled and "interrupt" in self.enabled:
            raise ValueError("duet cannot be combined with interrupt")
        self._emit(
            "config",
            text=f"profile {self.profile}",
            data={"who": "hud", "profile": self.profile, "enabled": list(self.enabled)},
        )
        return self.snapshot()

    def mute(self, persona: str, minutes: float) -> dict:
        self.muted[persona] = time.time() + minutes * 60
        self._emit(
            "config",
            persona,
            f"{persona.title()} muted for {minutes:g} min",
            data={"mute": persona, "minutes": minutes},
        )
        return self.snapshot()

    def unmute(self, persona: str) -> dict:
        self.muted.pop(persona, None)
        self._emit("config", persona, f"{persona.title()} unmuted", data={"unmute": persona})
        return self.snapshot()

    async def grant(self) -> dict:
        hand = self.pending_hand
        self.pending_hand = None
        if hand:
            self._state(hand["persona"], "SPEAKING")
            await asyncio.sleep(self.pace)
            self._emit(
                "say", hand["persona"], hand["text"], source=hand["source"], data={"granted": True}
            )
            self._state(hand["persona"], "LISTENING")
        return self.snapshot()

    # --------------------------------------------------------------- turn
    def _pick(self, text: str) -> Scenario:
        low = text.lower()
        for sc in SCENARIOS:
            if any(k in low for k in sc.keys):
                return sc
        return DEFAULT

    async def run_turn(self, text: str) -> None:
        async with self._lock:
            await self._turn(text)

    async def _turn(self, text: str) -> None:
        p = self.pace
        sc = self._pick(text)
        self.turn += 1
        self._emit(
            "turn",
            text=text,
            data={
                "phase": "start",
                "profile": self.profile,
                "enabled": list(self.enabled),
                "demo": True,
            },
        )
        low = text.lower()
        named = "root" if "root" in low else "finch" if "finch" in low else ""
        speaker = named or sc.speaker
        listener = "root" if speaker == "finch" else "finch"
        if "bidding" in self.enabled:
            scores = {speaker: 0.62, listener: 0.31}
            self._emit("bid", speaker, data={"scores": scores, "named": named})
        self.holder = speaker
        if "duet" in self.enabled:
            await self._duet(sc, speaker, listener)
            return
        self._state(speaker, "THINKING")
        self._state(listener, "LISTENING")
        await asyncio.sleep(p * 2)
        await self._tool(speaker, "cognee_recall", 0.42)
        if self.council is not None and sc.source.startswith("council"):
            await self._tool(speaker, "consult_council", 0.1)
            await self.council.run(text)

        act = sc.act
        if act and time.time() < self.muted.get(listener, 0) and act != "cut":
            act = ""
        if act and ACT_MODE[act] not in self.enabled:
            held = act
            act = "comment" if "comment" in self.enabled else ""
            self._emit(
                "suppressed",
                listener,
                sc.text,
                pressure=sc.pressure,
                source=sc.source,
                data={"action": held, "why": f"mode {ACT_MODE[held]} is off"},
            )

        if act in ("interject", "cut"):
            said = sc.reply[: sc.at + 1]
            await self._speak(speaker, said)
            self._state(listener, "INTERJECT")
            ev = self._emit(
                act,
                listener,
                sc.text,
                pressure=sc.pressure,
                source=sc.source,
                data={"dropped": sc.reply[sc.at + 1 :], "reason": sc.reason, "mode": ACT_MODE[act]},
            )
            self.spoken_today += 1
            self._state(speaker, "LISTENING")
            await asyncio.sleep(p * 3)
            self._emit("yield", speaker, "", reply_to=ev["id"])
            self.holder = listener
            await self._end()
            return

        await self._speak(speaker, sc.reply)
        if act == "hand":
            self.pending_hand = {"persona": listener, "text": sc.text, "source": sc.source}
            self._emit(
                "hand",
                listener,
                sc.text,
                pressure=sc.pressure,
                source=sc.source,
                data={"reason": sc.reason},
            )
        elif act == "comment":
            self._emit(
                "comment",
                listener,
                sc.text,
                pressure=sc.pressure,
                source=sc.source,
                data={"reason": sc.reason},
            )
        elif act in ("build", "add", "correct"):
            self._state(speaker, "LISTENING")
            self._state(listener, "THINKING")
            await asyncio.sleep(p * 2)
            self._state(listener, "SPEAKING")
            self._emit(
                act,
                listener,
                sc.text,
                pressure=sc.pressure,
                source=sc.source,
                data={"reason": sc.reason, "mode": ACT_MODE[act]},
            )
            self.spoken_today += 1
            for line in sc.exchange:
                self._state(listener, "LISTENING")
                self._state(speaker, "THINKING")
                await asyncio.sleep(p * 2)
                self._state(speaker, "SPEAKING")
                self._emit("build", speaker, line, data={"exchange": True})
        await self._end()

    async def _duet(self, sc: Scenario, first: str, second: str) -> None:
        lines = [
            (first, sc.reply[0]),
            (second, sc.text or "Agreed."),
            (first, "Then that is the plan."),
        ]
        prev = ""
        for who, line in lines:
            self._state(who, "SPEAKING")
            await asyncio.sleep(self.pace * 2)
            kind = "say" if not prev else "interject"
            self._emit(kind, who, line, data={"duet": True})
            self._state(who, "LISTENING")
            prev = who
        await self._end()

    async def _tool(self, persona: str, tool: str, seconds: float) -> None:
        self._emit("tool", persona, tool, data={"phase": "start", "tool": tool})
        await asyncio.sleep(seconds)
        self._emit(
            "tool",
            persona,
            tool,
            data={"phase": "end", "tool": tool, "ok": True, "latency": seconds},
        )

    async def _speak(self, persona: str, sentences: list[str]) -> None:
        if not sentences:
            return
        self._state(persona, "SPEAKING")
        for s in sentences:
            await asyncio.sleep(self.pace * 2)
            self._emit("say", persona, s)

    async def _end(self) -> None:
        await asyncio.sleep(self.pace)
        for persona in ("finch", "root"):
            self._state(persona, "LISTENING")
        self._emit("turn", data={"phase": "end"})


async def stream_until_end(queue: asyncio.Queue) -> AsyncIterator[dict[str, Any]]:
    while True:
        ev = await queue.get()
        yield ev
        if ev.get("kind") == "turn" and (ev.get("data") or {}).get("phase") == "end":
            return
