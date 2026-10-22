"""floor.toml: global limits, named profiles, the active profile.

Format (``04-interruption-strategies.md`` section 6.3, extended):

    [global]
    profile = "home_text"      # active profile at startup
    one_per_turn = true        # at most one spoken listener act per turn
    cooldown_s = 90
    daily_cap = 15
    fatigue_switch_at = 3
    threshold = 0.70
    max_exchanges = 2          # build mode: back-and-forth lines after the reply
    review_model = ""          # model for the listener review ("" = speaker model)
    model = ""                 # speaker model ("" = server default)

    [profiles.home_text]
    enabled = ["bidding", "interrupt", "build", "addendum", "comment", "correct"]
    threshold = 0.65           # any global key can be overridden per profile

Mode names: bidding, duet, interrupt, hard_cut, hand, comment, addendum, build,
correct. The 04 names (s7_bidding, s1_duet, s3_sentence_barge_in, s4_safety,
s5_raise_hand, s6_aside, s2_addendum, s8_post_hoc) are accepted as aliases.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ALIASES = {
    "s7_bidding": "bidding",
    "s1_duet": "duet",
    "s3_sentence_barge_in": "interrupt",
    "s4_safety": "hard_cut",
    "s5_raise_hand": "hand",
    "s6_aside": "comment",
    "s2_addendum": "addendum",
    "s8_post_hoc": "correct",
    "s9_build": "build",
}

ALL_MODES = (
    "bidding",
    "council",
    "duet",
    "interrupt",
    "hard_cut",
    "hand",
    "comment",
    "addendum",
    "build",
    "correct",
)

DEFAULT_PROFILES: dict[str, list[str]] = {
    # Text chat on the HUD: the personas talk to each other a lot.
    "home_text": ["bidding", "council", "interrupt", "build", "addendum", "comment", "correct"],
    # Voice at home (04 section 7): bid, cut at sentence boundaries, correct.
    "home_voice": ["bidding", "interrupt", "correct", "comment", "hard_cut"],
    # One call writes both voices.
    "duet": ["bidding", "duet", "comment"],
    # Focus or work hours: no spoken cuts, hands and asides only.
    "focus": ["bidding", "council", "hand", "comment", "hard_cut"],
    # Quiet hours.
    "night": ["comment", "hard_cut"],
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class FloorConfig:
    profile: str = "home_text"
    enabled: tuple[str, ...] = tuple(DEFAULT_PROFILES["home_text"])
    one_per_turn: bool = True
    cooldown_s: float = 90.0
    daily_cap: int = 15
    fatigue_switch_at: int = 3
    threshold: float = 0.70
    max_exchanges: int = 2
    review_model: str = ""
    model: str = ""
    cost_ceiling_eur: float = 150.0
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    def has(self, mode: str) -> bool:
        return mode in self.enabled

    def with_profile(self, name: str) -> "FloorConfig":
        if name not in self.profiles:
            raise ConfigError(f"unknown profile {name!r}")
        return _apply(replace(self, profile=name), self.profiles[name])

    def with_modes(self, enable=(), disable=()) -> "FloorConfig":
        modes = [m for m in self.enabled if m not in {_norm(d) for d in disable}]
        for m in enable:
            m = _norm(m)
            if m not in modes:
                modes.append(m)
        cfg = replace(self, enabled=tuple(modes))
        validate(cfg)
        return cfg


_GLOBAL_KEYS = {
    "one_per_turn": bool,
    "cooldown_s": float,
    "daily_cap": int,
    "fatigue_switch_at": int,
    "threshold": float,
    "max_exchanges": int,
    "review_model": str,
    "model": str,
    "cost_ceiling_eur": float,
}


def _norm(mode: str) -> str:
    mode = ALIASES.get(mode, mode)
    if mode not in ALL_MODES:
        raise ConfigError(f"unknown mode {mode!r}; known: {', '.join(ALL_MODES)}")
    return mode


def _apply(cfg: FloorConfig, section: dict[str, Any]) -> FloorConfig:
    changes: dict[str, Any] = {}
    for key, value in section.items():
        if key == "enabled":
            changes["enabled"] = tuple(_norm(m) for m in value)
        elif key in _GLOBAL_KEYS:
            changes[key] = _GLOBAL_KEYS[key](value)
    cfg = replace(cfg, **changes)
    validate(cfg)
    return cfg


def validate(cfg: FloorConfig) -> None:
    """Reject combinations that cannot work together (04 section 6.2)."""
    if cfg.has("duet") and cfg.has("interrupt"):
        raise ConfigError(
            f"profile {cfg.profile!r}: 'duet' and 'interrupt' cannot be enabled "
            "together; the duet already decides the switches"
        )
    if not 0 < cfg.threshold <= 1:
        raise ConfigError("threshold must be in (0, 1]")
    if cfg.max_exchanges < 0 or cfg.max_exchanges > 6:
        raise ConfigError("max_exchanges must be between 0 and 6")


def load(data: dict[str, Any] | None) -> FloorConfig:
    """Build the config from parsed floor.toml (None: defaults)."""
    data = data or {}
    profiles = {k: {"enabled": v} for k, v in DEFAULT_PROFILES.items()}
    for name, section in (data.get("profiles") or {}).items():
        profiles[name] = dict(section)
    base = FloorConfig(profiles=profiles)
    glob = dict(data.get("global") or {})
    active = glob.pop("profile", base.profile)
    base = _apply(base, glob)
    return base.with_profile(active)


def load_path(path: Path) -> FloorConfig:
    import tomllib

    if not path.is_file():
        return load(None)
    with path.open("rb") as fh:
        return load(tomllib.load(fh))
