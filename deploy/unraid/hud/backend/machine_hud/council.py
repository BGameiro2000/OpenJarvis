"""Council simulations: saved replays from the vault, and the demo council.

A simulation is saved by the council (custom package) as
``council/YYYY-MM-DD-NNNN.md`` with the full record in a fenced
``json council`` block. The HUD replays those files from the read-only mount,
so old simulations work even when OpenJarvis is down.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from .events import make_event

GODS = [
    {"id": "dionysus", "name": "Dionysus", "domain": "life"},
    {"id": "prometheus", "name": "Prometheus", "domain": "academic"},
    {"id": "daedalus", "name": "Daedalus", "domain": "inventions and projects"},
    {"id": "ananke", "name": "Ananke", "domain": "neurodivergence (pair)"},
    {"id": "ate", "name": "Ate", "domain": "neurodivergence (pair)"},
    {"id": "tyche", "name": "Tyche", "domain": "shopping"},
    {"id": "hermes", "name": "Hermes", "domain": "career"},
    {"id": "orpheus", "name": "Orpheus", "domain": "creativity"},
]
BLOCK = re.compile(r"```json council\s*\n(.*?)\n```", re.S)
SIM_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")


def read_sim(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
    except OSError:
        return None
    m = BLOCK.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("sim", path.stem)
    data["path"] = f"council/{path.name}"
    return data


def list_sims(vault: Path, limit: int = 100) -> list[dict[str, Any]]:
    folder = vault / "council"
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.md"), reverse=True)[: limit * 2]:
        d = read_sim(p)
        if not d:
            continue
        v = d.get("verdict") or {}
        out.append(
            {
                "sim": d["sim"],
                "question": d.get("question", ""),
                "created": d.get("created", ""),
                "verdict": v.get("decision", ""),
                "confidence": v.get("confidence"),
                "called": [r["god"] for r in d.get("router", []) if r.get("called")],
                "path": d["path"],
            }
        )
        if len(out) >= limit:
            break
    return out


def get_sim(vault: Path, sim: str) -> dict[str, Any] | None:
    if not SIM_NAME.match(sim):
        return None
    return read_sim(vault / "council" / f"{sim}.md")


# --------------------------------------------------------------------- demo
def demo_record(question: str, n: int) -> dict[str, Any]:
    low = question.lower()
    shopping = any(w in low for w in ("buy", "gpu", "a2000", "ram", "price", "euro", "€"))
    career = any(w in low for w in ("job", "offer", "career", "salary"))
    called = (
        {"tyche", "daedalus", "ananke", "ate"}
        if shopping
        else (
            {"hermes", "dionysus", "ananke", "ate"}
            if career
            else {"daedalus", "orpheus", "dionysus"}
        )
    )
    reasons = {
        "tyche": "price and listings in the question",
        "daedalus": "touches the home server plan",
        "ananke": "late night and 'now' in a purchase question",
        "ate": "pairs with Ananke",
        "hermes": "job offer in the question",
        "dionysus": "affects time and rest",
        "orpheus": "open ended, creative angle",
        "prometheus": "no academic angle",
    }
    router = [
        {
            "god": g["id"],
            "called": g["id"] in called,
            "score": 0.8 if g["id"] in called else 0.1,
            "reason": reasons.get(g["id"], "no match")
            if g["id"] in called
            else {
                "prometheus": "no academic angle",
                "hermes": "no career angle",
                "tyche": "nothing to buy",
                "orpheus": "not a creative question",
                "dionysus": "no effect on rest or fun",
                "daedalus": "no project angle",
                "ananke": "no impulse signal",
                "ate": "no impulse signal",
            }.get(g["id"], "no match"),
        }
        for g in GODS
    ]
    if shopping:
        r1 = [
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
                "sources": ["projects/home-jarvis.md", "wiki/home-server.md"],
            },
            {
                "god": "ananke",
                "stance": "flag",
                "confidence": 0.74,
                "argument": "Three hardware tabs since 02:00. This is the research loop pattern.",
                "sources": ["daily/2026-10-21.md"],
            },
            {
                "god": "ate",
                "stance": "flag",
                "confidence": 0.74,
                "argument": "Impulse window: a purchase decided after midnight is reversed half the time.",
                "sources": ["wiki/bernardo.md"],
            },
        ]
        r2 = [
            {
                "god": "tyche",
                "stance": "wait",
                "confidence": 0.55,
                "argument": "Listings stay up for days. Watching costs nothing; I park both.",
            },
            {
                "god": "daedalus",
                "stance": "against",
                "confidence": 0.74,
                "argument": "Agree with waiting. If anything, RAM first.",
            },
        ]
        verdict = {
            "decision": "WAIT",
            "confidence": 0.78,
            "summary": "Buy nothing tonight. Park both listings, decide Saturday morning; "
            "RAM first if only one.",
            "dissent": [{"god": "tyche", "argument": "The 310 euro listing is a good price."}],
            "flags": [{"god": "ate", "text": "late night research loop", "pressure": 0.74}],
        }
        branches = [
            {"name": "Buy the A2000 now", "score": 0.31, "note": "impulse window"},
            {"name": "RAM upgrade first", "score": 0.66, "note": "unblocks the plan"},
            {"name": "Wait until Saturday", "score": 0.78, "note": "same prices, clear head"},
        ]
    else:
        gods = sorted(called)
        r1 = [
            {
                "god": g,
                "stance": "for" if i % 2 == 0 else "wait",
                "confidence": 0.6,
                "argument": f"{g.title()} sees no reason against it, with care.",
                "sources": [],
            }
            for i, g in enumerate(gods)
        ]
        r2 = [
            {
                "god": gods[0],
                "stance": "for",
                "confidence": 0.65,
                "argument": "Nothing in round one changes my view.",
            }
        ]
        verdict = {
            "decision": "GO",
            "confidence": 0.64,
            "summary": "Go ahead, small first step.",
            "dissent": [],
            "flags": [],
        }
        branches = []
    for r in r1:
        r["round"] = 1
    for r in r2:
        r["round"] = 2
    sim = time.strftime("%Y-%m-%d-") + f"{n:04d}"
    return {
        "sim": sim,
        "question": question,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "router": router,
        "rounds": [r1, r2],
        "verdict": verdict,
        "branches": branches,
    }


class DemoCouncil:
    def __init__(self, publish, vault: Path, pace: float = 0.45) -> None:  # noqa: ANN001
        self.publish = publish
        self.vault = vault
        self.pace = pace
        self.sims: dict[str, dict[str, Any]] = {}
        self.n = 900

    def list(self) -> list[dict[str, Any]]:
        live = [
            {
                "sim": s["sim"],
                "question": s["question"],
                "created": s["created"],
                "verdict": s["verdict"]["decision"],
                "confidence": s["verdict"]["confidence"],
                "called": [r["god"] for r in s["router"] if r["called"]],
                "path": "",
            }
            for s in self.sims.values()
        ]
        return sorted(live, key=lambda s: s["sim"], reverse=True) + list_sims(self.vault)

    def get(self, sim: str) -> dict[str, Any] | None:
        return self.sims.get(sim) or get_sim(self.vault, sim)

    async def run(self, question: str) -> dict[str, Any]:
        self.n += 1
        rec = demo_record(question, self.n)
        sim = rec["sim"]
        called = [r for r in rec["router"] if r["called"]]
        self._emit(
            "consult",
            text=question,
            data={"sim": sim, "router": rec["router"], "called": [r["god"] for r in called]},
        )
        for rnd in rec["rounds"]:
            for pos in rnd:
                await asyncio.sleep(self.pace)
                self._emit("position", pos["god"], pos["argument"], data={**pos, "sim": sim})
        await asyncio.sleep(self.pace)
        self._emit("verdict", text=rec["verdict"]["summary"], data={**rec["verdict"], "sim": sim})
        self.sims[sim] = rec
        self._emit(
            "council_saved", data={"sim": sim, "path": f"council/{sim}.md (demo, not written)"}
        )
        return rec

    def _emit(self, kind: str, speaker: str = "", text: str = "", **kw: Any) -> None:
        self.publish(make_event(kind, speaker, text, **kw))
