"""Interaction modes: how the two personas engage with each other.

Every mode is a plugin registered in ``ModeRegistry`` (the same pattern
OpenJarvis uses for tools and agents). A mode only *proposes*; the floor
manager arbitrates and acts. Adding a mode means adding one class here or in
any module imported at startup.

| Mode | 04 name | Phase | What the listener does |
|---|---|---|---|
| bidding | S7 | pre_turn | picks who answers first (rules, no model call) |
| duet | S1 | pre_turn | one call writes both voices, cuts included |
| interrupt | S3 | during_turn | cuts in at a sentence boundary |
| hard_cut | S4 | during_turn | stops the speaker at once (safety allowlist only) |
| hand | S5 | during_turn | raises a hand, waits for Bernardo |
| comment | S6 | any | silent aside, shown not spoken |
| addendum | S2 | post_turn | one short line after the reply |
| build | new | post_turn | builds on the reply; the two may go back and forth |
| correct | S8 | post_turn | corrects a fact after the turn |
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from openjarvis.core.registry import RegistryBase

from ..personas import PERSONAS, other


class ModeRegistry(RegistryBase[Any]):
    """Registry of interaction mode plugins."""


# Strength of an action when proposals compete (higher wins). A blocked action
# degrades along DEGRADE to the next enabled weaker one.
STRENGTH = {
    "cut": 6,
    "interject": 5,
    "hand": 4,
    "correct": 3,
    "build": 2,
    "add": 1,
    "comment": 0,
}
DEGRADE = {
    "interject": ["hand", "comment"],
    "hand": ["comment"],
    "build": ["comment"],
    "add": ["comment"],
    "correct": ["comment"],
}
# Which mode must be enabled for an action to be used.
ACTION_MODE = {
    "cut": "hard_cut",
    "interject": "interrupt",
    "hand": "hand",
    "comment": "comment",
    "add": "addendum",
    "build": "build",
    "correct": "correct",
}
SPOKEN = {"cut", "interject", "add", "build", "correct"}


@dataclass
class Proposal:
    persona: str
    action: str
    pressure: float
    source: str = ""
    text: str = ""
    at: int = 0
    mode: str = ""
    reason: str = ""
    named: bool = False


@dataclass
class TurnContext:
    user_text: str
    speaker: str
    listener: str
    sentences: list[str]
    review: dict[str, Any]
    config: Any
    named: str = ""
    signals: dict[str, float] = field(default_factory=dict)


def named_persona(text: str) -> str:
    """'Root, what do you think?' -> 'root'. Empty if none or both named."""
    hits = [p for p in PERSONAS if re.search(rf"\b{p}\b", text, re.I)]
    return hits[0] if len(hits) == 1 else ""


SAFETY = re.compile(
    r"\b(smoke|fire|gas leak|stove (?:is )?on|door (?:is )?open|flood|"
    r"rm -rf|delete (?:all|everything)|wipe|format the)\b",
    re.I,
)
MONEY = re.compile(r"(?:€|\$|eur|usd)\s?(\d[\d.,]*)|(\d[\d.,]*)\s?(?:€|\$|eur|euros?|usd)", re.I)


def money_amounts(text: str) -> list[float]:
    out = []
    for a, b in MONEY.findall(text):
        raw = (a or b).replace(",", "")
        try:
            out.append(float(raw))
        except ValueError:
            pass
    return out


class Mode:
    name = ""
    phase = ""

    def propose(self, ctx: TurnContext) -> list[Proposal]:
        return []


def _from_review(ctx: TurnContext, act: str, action: str, mode: str) -> list[Proposal]:
    r = ctx.review
    if r.get("act") != act or not r.get("text"):
        return []
    pressure = max(r.get("pressure", 0.0), max(ctx.signals.values(), default=0.0))
    return [
        Proposal(
            persona=ctx.listener,
            action=action,
            pressure=pressure,
            source=r.get("source") or act,
            text=r["text"],
            at=max(0, min(r.get("at", 0), len(ctx.sentences) - 1)),
            mode=mode,
            reason=r.get("reason", ""),
        )
    ]


@ModeRegistry.register("bidding")
class Bidding(Mode):
    """S7: who answers first. Rules only: a named persona wins outright,
    otherwise domain keyword weights, ties go to Finch."""

    name, phase = "bidding", "pre_turn"

    @staticmethod
    def scores(text: str) -> dict[str, float]:
        low = text.lower()
        named = named_persona(text)
        scores = {}
        for p in PERSONAS.values():
            if named:
                scores[p.name] = 1.0 if p.name == named else 0.0
                continue
            hit = [w for k, w in p.domains.items() if k in low]
            scores[p.name] = round(min(1.0, 0.3 + sum(hit) / 2), 2) if hit else 0.3
        if not named and scores["finch"] == scores["root"]:
            scores["finch"] += 0.01
        return scores


@ModeRegistry.register("duet")
class Duet(Mode):
    """S1: one call writes both voices. Handled by the floor manager."""

    name, phase = "duet", "pre_turn"


@ModeRegistry.register("interrupt")
class Interrupt(Mode):
    """S3: the listener cuts in at the sentence boundary it chose."""

    name, phase = "interrupt", "during_turn"

    def propose(self, ctx):
        return _from_review(ctx, "interrupt", "interject", self.name)


@ModeRegistry.register("hard_cut")
class HardCut(Mode):
    """S4: immediate stop, for the safety allowlist only. Never for opinions."""

    name, phase = "hard_cut", "during_turn"

    def propose(self, ctx):
        for i, s in enumerate([ctx.user_text, *ctx.sentences]):
            if SAFETY.search(s):
                text = ctx.review.get("text") if ctx.review.get("act") == "interrupt" else ""
                return [
                    Proposal(
                        ctx.listener,
                        "cut",
                        1.0,
                        "safety",
                        text or "Stop. That touches something unsafe; let us check first.",
                        at=i - 1,
                        mode=self.name,  # -1: before any sentence
                    )
                ]
        return []


@ModeRegistry.register("hand")
class Hand(Mode):
    """S5: the listener asks for the floor and waits."""

    name, phase = "hand", "during_turn"

    def propose(self, ctx):
        props = _from_review(ctx, "hand", "hand", self.name)
        # With cutting in off (focus), a wish to interrupt becomes a hand.
        if "interrupt" not in ctx.config.enabled:
            props += _from_review(ctx, "interrupt", "hand", self.name)
        cost = ctx.signals.get("cost", 0.0)
        if not props and cost and ctx.listener == "root":
            # Rules first: a large amount makes Root want the floor even when
            # the review stayed quiet; with no line of its own it raises a hand.
            props.append(
                Proposal(
                    ctx.listener,
                    "hand",
                    cost,
                    "cost",
                    "Root wants to weigh in on the cost.",
                    mode=self.name,
                )
            )
        return props


@ModeRegistry.register("comment")
class Comment(Mode):
    """S6: a side note in the listener's colour, never spoken."""

    name, phase = "comment", "post_turn"

    def propose(self, ctx):
        # Catch-all at the lowest strength: any act whose own mode is off still
        # shows up as an aside instead of vanishing.
        act = ctx.review.get("act", "none")
        if act == "none" or not ctx.review.get("text"):
            return []
        own = {
            "interrupt": "interrupt",
            "hand": "hand",
            "add": "addendum",
            "build": "build",
            "correct": "correct",
            "comment": "comment",
        }
        if act != "comment" and own.get(act) in ctx.config.enabled:
            return []
        props = _from_review(ctx, act, "comment", self.name)
        for p in props:
            p.reason = p.reason or f"{act} mode is off"
        return props


@ModeRegistry.register("addendum")
class Addendum(Mode):
    """S2: one short line after the reply."""

    name, phase = "addendum", "post_turn"

    def propose(self, ctx):
        return _from_review(ctx, "add", "add", self.name)


@ModeRegistry.register("build")
class Build(Mode):
    """The listener extends the reply; the speaker may build back, up to
    ``max_exchanges`` lines in total."""

    name, phase = "build", "post_turn"

    def propose(self, ctx):
        return _from_review(ctx, "build", "build", self.name)


@ModeRegistry.register("correct")
class Correct(Mode):
    """S8: fix a factual error after the turn."""

    name, phase = "correct", "post_turn"

    def propose(self, ctx):
        props = _from_review(ctx, "correct", "correct", self.name)
        for p in props:
            p.pressure = max(p.pressure, 0.75)
        return props


def enabled_modes(config: Any, phase: str) -> list[Mode]:
    out = []
    for name in config.enabled:
        cls = ModeRegistry.get(name)
        if cls.phase == phase or (phase == "post_turn" and name == "comment"):
            out.append(cls())
    return out


__all__ = [
    "ACTION_MODE",
    "DEGRADE",
    "STRENGTH",
    "SPOKEN",
    "Bidding",
    "Mode",
    "ModeRegistry",
    "Proposal",
    "TurnContext",
    "enabled_modes",
    "money_amounts",
    "named_persona",
    "other",
]
