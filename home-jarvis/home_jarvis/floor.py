"""The floor manager: owns who speaks. The model proposes, this code decides.

A turn:

1. pre_turn: ``bidding`` picks the speaker; ``duet`` (if enabled) writes the
   whole exchange in one call and the rest of the turn is skipped.
2. The speaker's reply is generated, then split into sentences. Text runs
   ahead of audio, so every sentence can be checked before it is released.
3. The listener reviews the reply once and proposes at most one act; rules
   add signals (named persona, safety words, large amounts).
4. during_turn proposals (cut, interject, hand) are arbitrated; the chosen
   one fires at its sentence boundary, the speaker's remaining sentences are
   dropped, and the speaker yields.
5. Otherwise post_turn proposals (correct, build, add, comment) run after the
   last sentence. ``build`` lets the two go back and forth.
6. Limits apply throughout: one spoken listener act per turn, cooldown
   between interjections, mute, fatigue, daily cap, per-topic hysteresis. A
   blocked act degrades (interject -> hand -> comment) instead of vanishing.

Everything that happens is one ``FloorEvent``. The clock is injectable, so a
recorded conversation replays to the same decisions.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .brain import Brain
from .config import FloorConfig
from .events import EventLog, FloorEvent
from .modes import (
    ACTION_MODE,
    DEGRADE,
    SPOKEN,
    STRENGTH,
    Bidding,
    Proposal,
    TurnContext,
    enabled_modes,
    money_amounts,
    named_persona,
)
from .personas import PERSONAS, other

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_sentences(text: str) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    return [s.strip() for s in _SENT.split(text) if s.strip()]


@dataclass
class FloorState:
    holder: str = ""
    turn: int = 0
    last_interjection: float = -1e9
    muted_until: dict[str, float] = field(default_factory=dict)
    fatigue: dict[str, int] = field(default_factory=lambda: {"finch": 0, "root": 0})
    spoken_today: int = 0
    day: str = ""
    topic_bump: dict[str, float] = field(default_factory=dict)
    pending_hand: dict | None = None
    history: list[tuple[str, str]] = field(default_factory=list)


class FloorManager:
    def __init__(
        self,
        brain: Brain,
        config: FloorConfig,
        log: EventLog | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.brain = brain
        self.config = config
        self.clock = clock
        self.log = log or EventLog(clock=clock)
        self.state = FloorState()
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- utils
    def _emit(self, kind: str, speaker: str = "", text: str = "", **kw) -> FloorEvent:
        voice = PERSONAS[speaker].voice if speaker in PERSONAS and kind != "state" else ""
        return self.log.emit(
            FloorEvent(
                kind=kind,
                speaker=speaker,
                text=text,
                turn=self.state.turn,
                voice=voice,
                **kw,
            )
        )

    def _set_state(self, persona: str, state: str) -> None:
        self._emit("state", persona, data={"state": state})

    def _roll_day(self) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        if day != self.state.day:
            self.state.day = day
            self.state.spoken_today = 0
            self.state.fatigue = {"finch": 0, "root": 0}

    def _muted(self, persona: str) -> bool:
        return self.state.muted_until.get(persona, 0) > self.clock()

    # ------------------------------------------------------------- controls
    def set_config(self, config: FloorConfig, who: str = "api", how: str = "") -> None:
        with self._lock:
            self.config = config
        self._emit(
            "config",
            text=how or f"profile {config.profile}",
            data={
                "who": who,
                "profile": config.profile,
                "enabled": list(config.enabled),
            },
        )

    def mute(self, persona: str, minutes: float = 30, who: str = "user") -> None:
        self.state.muted_until[persona] = self.clock() + minutes * 60
        self.state.fatigue[persona] = self.state.fatigue.get(persona, 0) + 1
        self._emit(
            "config",
            persona,
            f"{PERSONAS[persona].title} muted for {minutes:g} min",
            data={"who": who, "mute": persona, "minutes": minutes},
        )

    def unmute(self, persona: str, who: str = "user") -> None:
        self.state.muted_until.pop(persona, None)
        self._emit("config", persona, f"{PERSONAS[persona].title} unmuted", data={"who": who})

    def feedback(self, persona: str, accepted: bool) -> None:
        """'Good point' lowers fatigue; 'stop' or talking over raises it."""
        delta = -1 if accepted else 1
        self.state.fatigue[persona] = max(0, self.state.fatigue.get(persona, 0) + delta)

    def grant(self) -> list[FloorEvent]:
        """Bernardo says 'go on' to a raised hand."""
        hand = self.state.pending_hand
        self.state.pending_hand = None
        if not hand:
            return []
        start = len(self.log.recent(10_000))
        self._set_state(hand["persona"], "SPEAKING")
        self._emit(
            "say",
            hand["persona"],
            hand["text"],
            source=hand.get("source", ""),
            data={"granted": True},
        )
        self.state.history.append((hand["persona"], hand["text"]))
        self._set_state(hand["persona"], "LISTENING")
        return self.log.recent(10_000)[start:]

    # ---------------------------------------------------------- arbitration
    def _blocked(self, p: Proposal, spoken_this_turn: int) -> str:
        cfg = self.config
        if ACTION_MODE[p.action] not in cfg.enabled:
            return "mode off"
        if p.action == "cut":
            return ""  # safety allowlist bypasses everything
        if p.action in SPOKEN or p.action == "hand":
            if self._muted(p.persona):
                return "muted"
        if p.action in SPOKEN:
            if cfg.one_per_turn and spoken_this_turn >= 1:
                return "one per turn"
            if self.state.spoken_today >= cfg.daily_cap:
                return "daily cap"
        if p.action == "interject":
            threshold = cfg.threshold + self.state.topic_bump.get(p.source, 0.0)
            if p.pressure < threshold and not p.named:
                return f"pressure {p.pressure:.2f} < {threshold:.2f}"
            if self.clock() - self.state.last_interjection < cfg.cooldown_s and not p.named:
                return "cooldown"
            if self.state.fatigue.get(p.persona, 0) > cfg.fatigue_switch_at:
                return "fatigue"
        return ""

    def _resolve(self, proposals: list[Proposal], spoken: int) -> Proposal | None:
        """Pick one proposal; degrade blocked ones; log suppressions."""
        order = sorted(
            proposals,
            key=lambda p: (
                p.action == "cut",
                p.named,
                STRENGTH[p.action],
                p.pressure,
                p.persona != self.state.holder,
            ),
            reverse=True,
        )
        for p in order:
            chain = [p.action, *DEGRADE.get(p.action, [])]
            for action in chain:
                candidate = Proposal(**{**p.__dict__, "action": action})
                reason = self._blocked(candidate, spoken)
                if not reason:
                    if action != p.action:
                        candidate.reason = f"degraded from {p.action}: " + (
                            self._blocked(p, spoken) or "blocked"
                        )
                    return candidate
            self._emit(
                "suppressed",
                p.persona,
                p.text,
                pressure=p.pressure,
                source=p.source,
                data={"action": p.action, "why": self._blocked(p, spoken)},
            )
        return None

    # ----------------------------------------------------------------- turn
    def run_turn(self, user_text: str) -> list[FloorEvent]:
        """Run one turn; returns the events it emitted (also in ``self.log``)."""
        with self._lock:
            start = len(self.log.recent(10_000))
            try:
                self._turn(user_text.strip())
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    "turn",
                    text=f"error: {type(exc).__name__}: {exc}",
                    data={"phase": "error"},
                )
            return self.log.recent(10_000)[start:]

    def _turn(self, user_text: str) -> None:
        cfg = self.config
        st = self.state
        self._roll_day()
        st.turn += 1
        self._emit(
            "turn",
            "",
            user_text,
            data={
                "phase": "start",
                "profile": cfg.profile,
                "enabled": list(cfg.enabled),
            },
        )
        st.history.append(("user", user_text))

        # pre_turn: who speaks first
        named = named_persona(user_text)
        if cfg.has("bidding"):
            scores = Bidding.scores(user_text)
            speaker = max(scores, key=scores.get)
            self._emit("bid", speaker, data={"scores": scores, "named": named})
        else:
            speaker = st.holder or "finch"
        listener = other(speaker)
        st.holder = speaker

        if cfg.has("duet"):
            self._run_duet(speaker, user_text)
            return

        self._set_state(speaker, "THINKING")
        self._set_state(listener, "LISTENING")
        reply = self.brain.reply(speaker, user_text, st.history)
        sentences = split_sentences(reply) or ["..."]

        # The listener reviews the whole reply before any of it is released.
        wants_review = any(
            m in cfg.enabled
            for m in ("interrupt", "hand", "comment", "addendum", "build", "correct")
        )
        review = (
            self.brain.review(listener, user_text, speaker, sentences, st.history)
            if wants_review and not self._muted(listener)
            else {"act": "none", "pressure": 0.0}
        )
        signals = self._signals(user_text, reply, listener, named)
        ctx = TurnContext(user_text, speaker, listener, sentences, review, cfg, named, signals)
        if named == listener and review.get("act") != "none":
            review["pressure"] = 1.0

        during = [p for m in enabled_modes(cfg, "during_turn") for p in m.propose(ctx)]
        for p in during:
            p.named = named == p.persona
        spoken = 0
        act = self._resolve(during, spoken) if during else None

        if act and act.action in ("cut", "interject"):
            cut_after = act.at
            self._speak(speaker, sentences[: cut_after + 1])
            dropped = sentences[cut_after + 1 :]
            self._set_state(listener, "INTERJECT")
            kind = "cut" if act.action == "cut" else "interject"
            ev = self._emit(
                kind,
                listener,
                act.text,
                pressure=round(act.pressure, 2),
                source=act.source,
                data={"dropped": dropped, "reason": act.reason, "mode": act.mode},
            )
            self._count_spoken(act)
            st.history.append((listener, act.text))
            self._set_state(speaker, "LISTENING")
            self._emit("yield", speaker, "", reply_to=ev.id)
            st.holder = listener
            self._set_state(listener, "LISTENING")
            self._end_turn()
            return

        self._speak(speaker, sentences)
        if act and act.action == "hand":
            st.pending_hand = {
                "persona": listener,
                "text": act.text,
                "source": act.source,
            }
            self._emit(
                "hand",
                listener,
                act.text,
                pressure=round(act.pressure, 2),
                source=act.source,
                data={"reason": act.reason},
            )
        elif act and act.action == "comment":
            self._emit(
                "comment",
                listener,
                act.text,
                pressure=round(act.pressure, 2),
                source=act.source,
                data={"reason": act.reason},
            )

        # post_turn: correct, build, add, comment
        post = [p for m in enabled_modes(cfg, "post_turn") for p in m.propose(ctx)]
        for p in post:
            p.named = named == p.persona
        chosen = self._resolve(post, spoken) if post else None
        if chosen:
            self._post_act(chosen, speaker, listener, user_text)
        self._end_turn()

    # ------------------------------------------------------------- helpers
    def _signals(self, user_text: str, reply: str, listener: str, named: str) -> dict:
        signals: dict[str, float] = {}
        amounts = money_amounts(user_text + " " + reply)
        if amounts and max(amounts) >= self.config.cost_ceiling_eur and listener == "root":
            signals["cost"] = 0.85
        if named == listener:
            signals["named"] = 1.0
        return signals

    def _speak(self, persona: str, sentences: list[str]) -> None:
        if not sentences:
            return
        self._set_state(persona, "SPEAKING")
        for s in sentences:
            self._emit("say", persona, s)
        self.state.history.append((persona, " ".join(sentences)))

    def _count_spoken(self, p: Proposal) -> None:
        self.state.spoken_today += 1
        if p.action == "interject":
            self.state.last_interjection = self.clock()
            # Hysteresis: the same topic needs more pressure next time.
            self.state.topic_bump[p.source] = self.state.topic_bump.get(p.source, 0.0) + 0.10

    def _post_act(self, p: Proposal, speaker: str, listener: str, user_text: str) -> None:
        st = self.state
        if p.action == "comment":
            self._emit(
                "comment",
                listener,
                p.text,
                pressure=round(p.pressure, 2),
                source=p.source,
                data={"reason": p.reason},
            )
            return
        self._count_spoken(p)
        self._set_state(listener, "SPEAKING")
        self._emit(
            p.action,
            listener,
            p.text,
            pressure=round(p.pressure, 2),
            source=p.source,
            data={"reason": p.reason, "mode": p.mode},
        )
        st.history.append((listener, p.text))
        if p.action != "build":
            self._set_state(listener, "LISTENING")
            return
        # Build: the two may go back and forth, each line building on the last.
        turn_holder, last = speaker, listener
        for _ in range(max(0, self.config.max_exchanges - 1)):
            self._set_state(last, "LISTENING")
            self._set_state(turn_holder, "THINKING")
            line = self.brain.reply(
                turn_holder,
                user_text,
                st.history,
                instruction=(
                    f"{PERSONAS[last].title} just built on the answer. Add one or two "
                    "sentences that build on that in turn, or reply exactly PASS if "
                    "there is nothing worth adding."
                ),
            ).strip()
            if not line or line.upper().startswith("PASS"):
                break
            self._set_state(turn_holder, "SPEAKING")
            self._emit("build", turn_holder, line, data={"exchange": True})
            st.history.append((turn_holder, line))
            turn_holder, last = last, turn_holder
        self._set_state(turn_holder, "LISTENING")
        self._set_state(last, "LISTENING")

    def _run_duet(self, first: str, user_text: str) -> None:
        self._set_state(first, "THINKING")
        lines = self.brain.duet(first, user_text, self.state.history) or [(first, "...")]
        prev = ""
        for who, text in lines:
            if prev and who != prev:
                self._set_state(prev, "LISTENING")
                kind = "interject"
                self._emit(kind, who, text, source="duet", data={"mode": "duet"})
            else:
                self._set_state(who, "SPEAKING")
                for s in split_sentences(text):
                    self._emit("say", who, s)
            self._set_state(who, "SPEAKING")
            self.state.history.append((who, text))
            prev = who
        self._end_turn()

    def _end_turn(self) -> None:
        for p in PERSONAS:
            self._set_state(p, "LISTENING")
        self._emit("turn", "", "", data={"phase": "end"})
        self.state.history = self.state.history[-24:]

    def snapshot(self) -> dict:
        now = self.clock()
        return {
            "profile": self.config.profile,
            "enabled": list(self.config.enabled),
            "threshold": self.config.threshold,
            "holder": self.state.holder,
            "turn": self.state.turn,
            "cooldown_left_s": max(
                0.0, self.config.cooldown_s - (now - self.state.last_interjection)
            ),
            "muted": {
                p: round((t - now) / 60, 1) for p, t in self.state.muted_until.items() if t > now
            },
            "fatigue": dict(self.state.fatigue),
            "spoken_today": self.state.spoken_today,
            "daily_cap": self.config.daily_cap,
            "pending_hand": self.state.pending_hand,
            "profiles": sorted(self.config.profiles),
        }
