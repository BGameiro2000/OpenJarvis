"""Check a routing envelope in code. Any failed check means escalate.

Each check is reported as ``{"name", "ok", "detail"}`` so the HUD can show
exactly what failed.
"""

from __future__ import annotations

import re
from typing import Any

from .config import ROUTES, IntakeConfig

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
BARE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalise(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The envelope with known keys and plain types; nothing is repaired."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = -1.0
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    clean_tags = []
    for tag in tags[:8]:
        tag = re.sub(r"[^a-z0-9-]+", "-", str(tag).lower()).strip("-")[:32]
        if tag and tag not in clean_tags:
            clean_tags.append(tag)
    return {
        "route": str(raw.get("route") or "").strip(),
        "slug": str(raw.get("slug") or "").strip(),
        "confidence": confidence,
        "tags": clean_tags,
        "target_list": str(raw.get("target_list") or "").strip(),
        "why": " ".join(str(raw.get("why") or "").split())[:300],
    }


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": "" if ok else detail}


def validate(envelope: dict[str, Any], cfg: IntakeConfig) -> list[dict[str, Any]]:
    route = envelope.get("route", "")
    target = envelope.get("target_list", "")
    spec = cfg.find(target)
    confidence = envelope.get("confidence", 0.0)
    return [
        _check("route allowed", route in ROUTES, f"{route!r} is not one of {', '.join(ROUTES)}"),
        _check("route files", route != "escalate", "the classifier chose to escalate"),
        _check(
            "target_list bare filename",
            bool(BARE_FILE.match(target)) and ".." not in target,
            f"{target!r} is not a bare file name",
        ),
        _check("list in menu", spec is not None, f"{target!r} is not in the menu"),
        _check(
            "list matches route",
            spec is not None and spec.route == route,
            f"{target!r} is a {spec.route} list, not {route}" if spec else "no such list",
        ),
        _check(
            "slug safe",
            bool(SLUG.match(envelope.get("slug", ""))),
            f"{envelope.get('slug', '')!r} does not match {SLUG.pattern}",
        ),
        _check(
            "confidence",
            isinstance(confidence, float) and cfg.min_confidence <= confidence <= 1.0,
            f"{confidence} is outside {cfg.min_confidence} to 1.0"
            if isinstance(confidence, float) and confidence >= 0
            else "confidence is not a number between 0 and 1",
        ),
        _check("why present", bool(envelope.get("why")), "no reason given"),
    ]


def passed(checks: list[dict[str, Any]]) -> bool:
    return all(c["ok"] for c in checks)


def failures(checks: list[dict[str, Any]]) -> str:
    return "; ".join(f"{c['name']}: {c['detail']}" for c in checks if not c["ok"])
