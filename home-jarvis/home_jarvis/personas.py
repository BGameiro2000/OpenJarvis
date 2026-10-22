"""Finch and Root: two personas over one shared memory.

Each persona differs in its SOUL file, voice and tendencies. Both read the
same ``MEMORY.md`` and ``USER.md`` (upstream's ``persona_name`` would give each
its own memory, which is not what we want).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Persona:
    name: str
    title: str
    voice: str
    traits: str
    style: str
    # Domain hints for floor bidding: keyword -> weight. Starting values to tune.
    domains: dict[str, float] = field(default_factory=dict)

    def soul_path(self, vault: Path) -> Path:
        return vault / "personas" / self.name / "SOUL.md"


FINCH = Persona(
    name="finch",
    title="Finch",
    voice="finch-v1",
    traits="analytical, cautious, administrative",
    style=(
        "You are Finch. Precise, measured, slightly formal. You plan, research, "
        "weigh options and state your confidence. You prefer evidence to instinct."
    ),
    domains={
        "plan": 0.6,
        "research": 0.7,
        "paper": 0.7,
        "study": 0.6,
        "career": 0.6,
        "compare": 0.5,
        "why": 0.4,
        "explain": 0.5,
        "physics": 0.7,
        "project": 0.4,
        "should i": 0.5,
        " or ": 0.3,
    },
)

ROOT = Persona(
    name="root",
    title="Root",
    voice="root-v1",
    traits="direct, protective, analog interface",
    style=(
        "You are Root. Direct, quick, protective of Bernardo, a little irreverent. "
        "You act, you warn, you watch spending, routines and security. Short sentences."
    ),
    domains={
        "buy": 0.8,
        "spend": 0.8,
        "price": 0.6,
        "cost": 0.6,
        "security": 0.7,
        "password": 0.7,
        "routine": 0.6,
        "tonight": 0.4,
        "now": 0.3,
        "remind": 0.5,
        "do it": 0.5,
        "order": 0.6,
    },
)

PERSONAS = {p.name: p for p in (FINCH, ROOT)}


def other(name: str) -> str:
    return "root" if name == "finch" else "finch"


def read_capped(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n[...]"


def system_prompt(persona: Persona, vault: Path, *, role: str) -> str:
    """System prompt for one persona. ``role`` is ``speaker`` or ``listener``."""
    partner = PERSONAS[other(persona.name)]
    soul = read_capped(persona.soul_path(vault), 4000) or read_capped(vault / "SOUL.md", 4000)
    memory = read_capped(vault / "MEMORY.md", 2500)
    user = read_capped(vault / "USER.md", 1500)
    parts = [
        persona.style,
        f"You share the floor with {partner.title} ({partner.traits}). "
        "Bernardo hears both of you. Never pretend to be the other one.",
        "Plain English. No em dashes. Keep spoken replies short: two to five "
        "sentences unless asked for more.",
    ]
    if role == "listener":
        parts.append(
            f"Right now {partner.title} holds the floor and you are listening. "
            "You may cut in, add, build on, comment or correct, only when it "
            "genuinely helps Bernardo. Most of the time, stay quiet."
        )
    if soul:
        parts.append("## Soul\n" + soul)
    if memory:
        parts.append("## Shared memory\n" + memory)
    if user:
        parts.append("## About Bernardo\n" + user)
    return "\n\n".join(parts)
