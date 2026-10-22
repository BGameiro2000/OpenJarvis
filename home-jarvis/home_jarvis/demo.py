"""Scripted conversations for the HUD demo and tests. No model, no key.

Each scenario shows one interaction mode. The scenario is picked by keyword,
else round-robin, so any message gets a plausible demo turn.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Scenario:
    key: str
    words: tuple[str, ...]
    replies: dict[str, list[str]]
    review: dict[str, Any]
    exchange: list[str] = field(default_factory=list)


SCENARIOS = [
    Scenario(
        "interrupt",
        ("gpu", "a2000", "buy", "ram"),
        {
            "finch": [
                "I put it to the council. Tyche found two used listings under your "
                "ceiling. Daedalus says the RAM upgrade unblocks more of the current "
                "plan than the GPU does. If you want both, the A2000 listing at 310 "
                "euros is the better of the two."
            ]
        },
        {
            "act": "interrupt",
            "at": 1,
            "pressure": 0.74,
            "source": "council:ate",
            "reason": "late night research loop, impulse window",
            "text": "Interjecting. Ate flagged a late night research loop, three hardware "
            "tabs since 02:00. Buy nothing tonight.",
        },
    ),
    Scenario(
        "build",
        ("paper", "results", "write", "structure", "plan"),
        {
            "finch": [
                "Lead with the cross section you are most sure of, then the systematic "
                "budget. Keep the comparison with previous measurements for the end."
            ],
            "root": [],
        },
        {
            "act": "build",
            "pressure": 0.55,
            "source": "next step",
            "text": "And block two mornings for it this week. You write results best "
            "before noon; afternoons go to plots.",
        },
        exchange=[
            "Good. Then draft the systematic table first on Tuesday; the text "
            "follows the table, not the other way round.",
        ],
    ),
    Scenario(
        "add",
        ("remind", "tomorrow", "call", "meeting"),
        {"root": ["Done. Reminder set for tomorrow at nine, and I put the call link in the note."]},
        {
            "act": "add",
            "pressure": 0.4,
            "source": "context",
            "text": "One addition: the meeting notes from last week are in "
            "projects/home-jarvis.md, in case they ask about the timeline.",
        },
    ),
    Scenario(
        "correct",
        ("when", "date", "deadline", "due"),
        {"finch": ["The review is due on the 30th of November, so you have five weeks."]},
        {
            "act": "correct",
            "pressure": 0.8,
            "source": "memory",
            "text": "Correction: memory says the deadline moved to mid December, not "
            "the 30th of November.",
        },
    ),
    Scenario(
        "comment",
        ("weather", "music", "film", "book"),
        {"finch": ["Light rain until four, then clear. A good evening for a walk."]},
        {
            "act": "comment",
            "pressure": 0.2,
            "source": "aside",
            "text": "Aside: the umbrella is still in the car.",
        },
    ),
    Scenario(
        "hand",
        ("career", "job", "offer", "salary"),
        {
            "finch": [
                "The offer is stronger on research time and weaker on salary. I would "
                "ask for the salary review clause in writing before deciding."
            ]
        },
        {
            "act": "hand",
            "pressure": 0.6,
            "source": "negotiation",
            "text": "Root wants to add a number to anchor that negotiation.",
        },
    ),
]


class DemoBrain:
    def __init__(self) -> None:
        self._cycle = itertools.cycle(SCENARIOS)
        self._current: Scenario | None = None
        self._exchange: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def _pick(self, text: str) -> Scenario:
        low = text.lower()
        for sc in SCENARIOS:
            if any(w in low for w in sc.words):
                return sc
        return next(self._cycle)

    def reply(self, persona, user_text, history, *, instruction=""):
        self.calls.append(("reply", persona))
        if "built on the answer" in instruction:  # a build-back line
            return self._exchange.pop(0) if self._exchange else "PASS"
        sc = self._current = self._pick(user_text)
        self._exchange = list(sc.exchange)
        lines = sc.replies.get(persona) or ["Noted. Give me a moment to check the notes."]
        return lines[0]

    def review(self, listener, user_text, speaker, sentences, history):
        self.calls.append(("review", listener))
        sc = self._current or self._pick(user_text)
        review = dict(sc.review)
        if review.get("act") == "interrupt":
            review["at"] = min(review.get("at", 0), max(0, len(sentences) - 2))
        return review

    def ask(self, system, prompt, max_tokens=600):
        """Canned council rounds, so the council runs in demo mode too."""
        self.calls.append(("ask", prompt.split(".", 1)[0]))
        shopping = any(w in prompt.lower() for w in ("a2000", "gpu", "buy", "ram"))
        if prompt.startswith("ROUND 1"):
            if shopping:
                return json.dumps(
                    {
                        "positions": [
                            {
                                "god": "tyche",
                                "stance": "for",
                                "confidence": 0.62,
                                "argument": "Two used A2000 listings under the 350 euro ceiling; the 310 one is fair.",
                                "sources": ["shopping/watch.md"],
                            },
                            {
                                "god": "daedalus",
                                "stance": "against",
                                "confidence": 0.71,
                                "argument": "RAM unblocks more of the current plan: Cognee and the index are memory bound.",
                                "sources": ["wiki/home-server.md"],
                            },
                            {
                                "god": "ananke",
                                "stance": "flag",
                                "confidence": 0.74,
                                "argument": "Three hardware tabs since 02:00. This is the research loop pattern.",
                            },
                            {
                                "god": "ate",
                                "stance": "flag",
                                "confidence": 0.74,
                                "argument": "A purchase decided after midnight is reversed half the time.",
                            },
                        ]
                    }
                )
            return json.dumps(
                {
                    "positions": [
                        {
                            "god": g,
                            "stance": "for",
                            "confidence": 0.6,
                            "argument": "No reason against it, with care.",
                        }
                        for g in (
                            "daedalus",
                            "dionysus",
                            "prometheus",
                            "hermes",
                            "orpheus",
                            "tyche",
                        )
                    ]
                }
            )
        if prompt.startswith("ROUND 2"):
            if shopping:
                return json.dumps(
                    {
                        "rebuttals": [
                            {
                                "god": "tyche",
                                "stance": "wait",
                                "confidence": 0.55,
                                "argument": "Listings stay up for days. Watching costs nothing; I park both.",
                            },
                        ]
                    }
                )
            return json.dumps({"rebuttals": []})
        if shopping:
            return json.dumps(
                {
                    "decision": "WAIT",
                    "confidence": 0.78,
                    "summary": "Buy nothing tonight. Park both listings, decide Saturday morning; RAM first if only one.",
                    "dissent": [
                        {"god": "tyche", "argument": "The 310 euro listing is a good price."}
                    ],
                    "flags": [
                        {
                            "god": "ate",
                            "text": "a late night research loop, three hardware tabs since 02:00",
                            "pressure": 0.74,
                        }
                    ],
                    "branches": [
                        {"name": "Buy the A2000 now", "score": 0.31, "note": "impulse window"},
                        {"name": "RAM upgrade first", "score": 0.66, "note": "unblocks the plan"},
                        {"name": "Wait until Saturday", "score": 0.78, "note": "same prices"},
                    ],
                }
            )
        return json.dumps(
            {
                "decision": "GO",
                "confidence": 0.64,
                "summary": "Go ahead, small first step.",
                "dissent": [],
                "flags": [],
                "branches": [],
            }
        )

    def duet(self, first, user_text, history):
        self.calls.append(("duet", first))
        return [
            (first, "Short answer: yes, but not tonight."),
            (
                "root" if first == "finch" else "finch",
                "Because tonight is when the bad purchases happen.",
            ),
            (first, "Saturday morning, then, with a fixed ceiling."),
        ]


def demo_brain() -> DemoBrain:
    return DemoBrain()
