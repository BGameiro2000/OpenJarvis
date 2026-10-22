"""intake.toml: dry run, confidence floor, classifier model, the list menu.

Format (every key optional; these are the defaults):

    dry_run = true             # a scan only plans unless apply is asked for
    min_confidence = 0.60      # below this the classifier's answer escalates
    classifier_model = ""      # "" = the server model
    classify_ai_chats = false  # true: AI chats go to the classifier too

    [[lists]]
    list = "next-actions.md"   # the file name, also what the classifier answers
    route = "gtd_action"       # one of ROUTES except "escalate"
    folder = "gtd"             # the list lives at <folder>/<list> in the vault
    scope = "single next steps Bernardo can do himself"

When the file has no ``[[lists]]`` the built-in menu below is used. The menu
is closed: the sorter never creates a list the menu does not name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ROUTES = ("project_shaped", "gtd_action", "idea_capture", "reference", "escalate")
FILING_ROUTES = ROUTES[:-1]

# The folder a new list goes to when the caller does not say.
DEFAULT_FOLDERS = {
    "gtd_action": "gtd",
    "idea_capture": "ideas",
    "project_shaped": "potential_projects",
    "reference": "reference",
}

LIST_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}\.md$")
_PART = r"[A-Za-z0-9_-][A-Za-z0-9_ .-]{0,63}"
FOLDER_NAME = re.compile(rf"^{_PART}(/{_PART}){{0,3}}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ListSpec:
    list: str
    route: str
    folder: str
    scope: str = ""

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.list}"

    def to_json(self) -> dict[str, str]:
        return {"list": self.list, "route": self.route, "folder": self.folder, "scope": self.scope}


DEFAULT_MENU: tuple[ListSpec, ...] = (
    ListSpec("next-actions.md", "gtd_action", "gtd", "single next steps Bernardo can do himself"),
    ListSpec("waiting-for.md", "gtd_action", "gtd", "things Bernardo waits on from someone else"),
    ListSpec("someday.md", "gtd_action", "gtd", "actions worth keeping but not now"),
    ListSpec("backlog.md", "idea_capture", "ideas", "feature ideas for an existing project"),
    ListSpec("ideas.md", "idea_capture", "ideas", "loose ideas not tied to a project"),
    ListSpec(
        "potential-projects.md",
        "project_shaped",
        "potential_projects",
        "outcomes that need more than one step",
    ),
    ListSpec("reference.md", "reference", "reference", "facts and notes to keep, no action"),
    ListSpec("chats.md", "reference", "reference", "chats with people"),
    ListSpec("ai-chats.md", "reference", "reference", "conversations with AI assistants"),
)


@dataclass(frozen=True)
class IntakeConfig:
    dry_run: bool = True
    min_confidence: float = 0.60
    classifier_model: str = ""
    classify_ai_chats: bool = False
    lists: tuple[ListSpec, ...] = field(default=DEFAULT_MENU)

    def find(self, name: str) -> ListSpec | None:
        return next((spec for spec in self.lists if spec.list == name), None)

    def with_list(self, spec: ListSpec) -> IntakeConfig:
        check_list(spec)
        if self.find(spec.list):
            raise ConfigError(f"list {spec.list!r} is already in the menu")
        return replace(self, lists=(*self.lists, spec))


def check_list(spec: ListSpec) -> None:
    if not LIST_NAME.match(spec.list):
        raise ConfigError(f"list name {spec.list!r} must be a bare lowercase .md file name")
    if spec.route not in FILING_ROUTES:
        raise ConfigError(f"list route must be one of {', '.join(FILING_ROUTES)}")
    if not FOLDER_NAME.match(spec.folder) or ".." in spec.folder.split("/"):
        raise ConfigError(f"list folder {spec.folder!r} is not a safe relative folder")
    if spec.folder.split("/")[0] == "inbox":
        raise ConfigError("lists cannot live in the inbox")


def load(data: dict[str, Any] | None) -> IntakeConfig:
    """Build the config from parsed intake.toml (None: defaults)."""
    data = data or {}
    changes: dict[str, Any] = {}
    if "dry_run" in data:
        changes["dry_run"] = bool(data["dry_run"])
    if "min_confidence" in data:
        value = float(data["min_confidence"])
        if not 0 <= value <= 1:
            raise ConfigError("min_confidence must be between 0 and 1")
        changes["min_confidence"] = value
    if "classifier_model" in data:
        changes["classifier_model"] = str(data["classifier_model"])
    if "classify_ai_chats" in data:
        changes["classify_ai_chats"] = bool(data["classify_ai_chats"])
    if data.get("lists"):
        lists: list[ListSpec] = []
        for raw in data["lists"]:
            route = str(raw.get("route", ""))
            spec = ListSpec(
                list=str(raw.get("list", "")),
                route=route,
                folder=str(raw.get("folder") or DEFAULT_FOLDERS.get(route, "")),
                scope=str(raw.get("scope", "")),
            )
            check_list(spec)
            if any(s.list == spec.list for s in lists):
                raise ConfigError(f"list {spec.list!r} appears twice")
            lists.append(spec)
        changes["lists"] = tuple(lists)
    return replace(IntakeConfig(), **changes)


def config_path() -> Path:
    """``OPENJARVIS_INTAKE_CONFIG``, else ``<custom dir>/intake.toml``."""
    if os.environ.get("OPENJARVIS_INTAKE_CONFIG"):
        return Path(os.environ["OPENJARVIS_INTAKE_CONFIG"])
    try:
        from openjarvis_unraid_hooks import custom_dir
    except ImportError:
        home = os.environ.get("OPENJARVIS_HOME", "/data")
        return Path(os.environ.get("OPENJARVIS_CUSTOM_DIR", f"{home}/custom")) / "intake.toml"
    return custom_dir() / "intake.toml"


def _read(path: Path) -> dict[str, Any] | None:
    import tomllib

    if not path.is_file():
        return None
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_path(path: Path) -> IntakeConfig:
    return load(_read(path))


def _block(spec: ListSpec) -> str:
    # JSON strings are valid TOML basic strings (same escapes).
    return (
        "\n[[lists]]\n"
        f"list = {json.dumps(spec.list)}\n"
        f"route = {json.dumps(spec.route)}\n"
        f"folder = {json.dumps(spec.folder)}\n"
        f"scope = {json.dumps(spec.scope)}\n"
    )


def persist_list(path: Path, cfg: IntakeConfig, spec: ListSpec) -> IntakeConfig:
    """Add ``spec`` to the menu and to intake.toml.

    The file is only appended to. When it has no ``[[lists]]`` yet (the menu
    is the built-in one), the whole current menu is written first so adding a
    list never drops the others.
    """
    new = cfg.with_list(spec)
    data = _read(path)
    text = ""
    if data is None:
        text = (
            "# Written by the intake sorter when a list was added. See intake.example.toml.\n"
            f"dry_run = {'true' if cfg.dry_run else 'false'}\n"
        )
    if not (data or {}).get("lists"):
        text += "".join(_block(s) for s in cfg.lists)
    text += _block(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
    return new
