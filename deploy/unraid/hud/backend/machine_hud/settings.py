"""Environment settings. Keys stay here and never reach the browser."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openjarvis_url: str = "http://openjarvis:8000"
    openjarvis_key: str = field(default="", repr=False)
    cognee_url: str = "http://cognee:8000"
    cognee_key: str = field(default="", repr=False)
    auth: str = "off"
    user: str = "bernardo"
    password: str = field(default="", repr=False)
    owner: str = "Bernardo"
    demo: bool = False
    plain_persona: str = "finch"
    static_dir: Path = Path("/app")
    vault: Path = Path("/vault")

    @classmethod
    def from_env(cls) -> Settings:
        password = os.environ.get("HUD_PASSWORD", "")
        auth = os.environ.get("HUD_AUTH", "").strip().lower() or ("basic" if password else "off")
        return cls(
            openjarvis_url=os.environ.get("OPENJARVIS_URL", cls.openjarvis_url).rstrip("/"),
            openjarvis_key=os.environ.get("OPENJARVIS_API_KEY", ""),
            cognee_url=os.environ.get("COGNEE_URL", cls.cognee_url).rstrip("/"),
            cognee_key=os.environ.get("COGNEE_API_KEY", ""),
            auth=auth,
            user=os.environ.get("HUD_USER", cls.user),
            password=password,
            owner=os.environ.get("HUD_OWNER", cls.owner),
            demo=_flag("HUD_DEMO"),
            plain_persona=os.environ.get("HUD_PLAIN_PERSONA", cls.plain_persona),
            static_dir=Path(os.environ.get("HUD_STATIC_DIR", str(cls.static_dir))),
            vault=Path(os.environ.get("HUD_VAULT", str(cls.vault))),
        )
