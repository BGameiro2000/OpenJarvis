"""What the floor manager asks the language model to do.

Three questions only:

- ``reply``: a persona answers (or builds on what the other one said).
- ``review``: the listening persona reads the speaker's reply, sentence by
  sentence, and proposes at most one act (interrupt, add, build, comment,
  correct, hand) with a pressure and the line it would say. One call per turn.
- ``duet``: one call writes both voices as a short script.

``EngineBrain`` uses the OpenJarvis engine. ``ScriptedBrain`` replays canned
answers for tests and the HUD demo, with no model at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from .personas import PERSONAS, other, system_prompt

ACTS = ("none", "interrupt", "add", "build", "comment", "correct", "hand")

REVIEW_INSTRUCTIONS = """\
You are listening while {speaker} answers Bernardo. Read the numbered sentences
of the reply. Decide whether you should act, and how. Options:

- "interrupt": cut in after sentence N because continuing would mislead,
  cost money, ignore a risk or a flag, or contradict memory. High bar.
- "build": after the reply, extend it with something genuinely new (a next
  step, an example, the angle {speaker} missed).
- "add": after the reply, one short caveat or reminder.
- "correct": a fact in the reply is wrong; give the correction.
- "comment": a quiet side note, shown but not spoken.
- "hand": you want to say something but it can wait for permission.
- "none": nothing worth adding. This is the usual answer.

Return only JSON:
{{"act": "...", "at": N, "pressure": 0.0-1.0, "source": "short tag",
  "reason": "why, one line", "text": "what you would say, one or two sentences"}}
"at" is the 0-based sentence index after which you would cut in (interrupt only).
Pressure means how much Bernardo would lose if you stayed quiet.
"""

DUET_INSTRUCTIONS = """\
Write the answer as a short exchange between Finch and Root, in their voices,
each line starting with [FINCH] or [ROOT]. {first} speaks first. Two to five
lines in total. They may cut in, disagree, or build on each other, but only
when it adds something; they share one memory and never repeat each other.
"""


class Brain(Protocol):
    def reply(
        self,
        persona: str,
        user_text: str,
        history: list[tuple[str, str]],
        *,
        instruction: str = "",
    ) -> str: ...

    def review(
        self,
        listener: str,
        user_text: str,
        speaker: str,
        sentences: list[str],
        history: list[tuple[str, str]],
    ) -> dict[str, Any]: ...

    def duet(
        self, first: str, user_text: str, history: list[tuple[str, str]]
    ) -> list[tuple[str, str]]: ...


def parse_review(raw: str) -> dict[str, Any]:
    """Parse the review JSON, tolerating code fences and prose around it."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {"act": "none", "pressure": 0.0, "reason": "unparsable review"}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"act": "none", "pressure": 0.0, "reason": "unparsable review"}
    act = str(data.get("act", "none")).lower()
    data["act"] = act if act in ACTS else "none"
    try:
        data["pressure"] = max(0.0, min(1.0, float(data.get("pressure", 0.0))))
    except (TypeError, ValueError):
        data["pressure"] = 0.0
    try:
        data["at"] = int(data.get("at", 0))
    except (TypeError, ValueError):
        data["at"] = 0
    data["text"] = str(data.get("text", "")).strip()
    data["source"] = str(data.get("source", "")).strip()[:40]
    return data


def parse_duet(raw: str) -> list[tuple[str, str]]:
    lines = []
    for m in re.finditer(r"\[(FINCH|ROOT)\]\s*(.+?)(?=\n?\[(?:FINCH|ROOT)\]|\Z)", raw or "", re.S):
        text = " ".join(m.group(2).split())
        if text:
            lines.append((m.group(1).lower(), text))
    return lines


def _history_messages(history: list[tuple[str, str]]) -> list[dict[str, str]]:
    msgs = []
    for who, text in history[-12:]:
        if who == "user":
            msgs.append({"role": "user", "content": text})
        else:
            msgs.append({"role": "assistant", "content": f"[{who.upper()}] {text}"})
    return msgs


class EngineBrain:
    """Calls ``engine.generate`` with persona prompts built from the vault."""

    def __init__(self, engine: Any, model: str, vault: Path, review_model: str = ""):
        self.engine = engine
        self.model = model
        self.review_model = review_model or model
        self.vault = vault

    def _call(self, messages: list[dict[str, str]], model: str, max_tokens: int) -> str:
        from openjarvis.core.types import Message, Role

        msgs = [Message(role=Role(m["role"]), content=m["content"]) for m in messages]
        out = self.engine.generate(msgs, model=model, temperature=0.6, max_tokens=max_tokens)
        return str(out.get("content", "")).strip()

    def reply(self, persona, user_text, history, *, instruction=""):
        system = system_prompt(PERSONAS[persona], self.vault, role="speaker")
        if instruction:
            system += "\n\n" + instruction
        messages = [{"role": "system", "content": system}, *_history_messages(history)]
        messages.append({"role": "user", "content": user_text})
        text = self._call(messages, self.model, 600)
        return re.sub(r"^\[(FINCH|ROOT)\]\s*", "", text, flags=re.I)

    def review(self, listener, user_text, speaker, sentences, history):
        system = system_prompt(PERSONAS[listener], self.vault, role="listener")
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
        prompt = (
            REVIEW_INSTRUCTIONS.format(speaker=PERSONAS[speaker].title)
            + f"\nBernardo asked: {user_text}\n\n{PERSONAS[speaker].title}'s reply:\n{numbered}"
        )
        messages = [
            {"role": "system", "content": system},
            *_history_messages(history),
            {"role": "user", "content": prompt},
        ]
        return parse_review(self._call(messages, self.review_model, 300))

    def duet(self, first, user_text, history):
        system = (
            system_prompt(PERSONAS["finch"], self.vault, role="speaker")
            + "\n\n"
            + DUET_INSTRUCTIONS.format(first=PERSONAS[first].title)
        )
        messages = [{"role": "system", "content": system}, *_history_messages(history)]
        messages.append({"role": "user", "content": user_text})
        return parse_duet(self._call(messages, self.model, 700))


class ScriptedBrain:
    """Deterministic brain for tests and the HUD demo.

    ``replies``: persona -> list of replies (consumed in order).
    ``reviews``: list of review dicts (consumed in order; default "none").
    ``duets``: list of [(speaker, text), ...] scripts.
    """

    def __init__(self, replies=None, reviews=None, duets=None):
        self.replies = {k: list(v) for k, v in (replies or {}).items()}
        self.reviews = list(reviews or [])
        self.duets = list(duets or [])
        self.calls: list[tuple[str, str]] = []

    def reply(self, persona, user_text, history, *, instruction=""):
        self.calls.append(("reply", persona))
        queue = self.replies.get(persona) or []
        return queue.pop(0) if queue else "PASS"

    def review(self, listener, user_text, speaker, sentences, history):
        self.calls.append(("review", listener))
        raw = self.reviews.pop(0) if self.reviews else {"act": "none"}
        return parse_review(json.dumps(raw))

    def duet(self, first, user_text, history):
        self.calls.append(("duet", first))
        return self.duets.pop(0) if self.duets else [(first, "PASS")]


__all__ = [
    "ACTS",
    "Brain",
    "EngineBrain",
    "ScriptedBrain",
    "parse_duet",
    "parse_review",
    "other",
]
