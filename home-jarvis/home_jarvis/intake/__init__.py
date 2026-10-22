"""Intake sorter: everything dropped in the vault inbox ends up filed, or asked about.

Pipeline: capture into ``inbox/``, detect by content, extract into items,
route by rules first, classify the rest with the model, validate in code,
file, dedupe. Dry run is the default. See ``docs/INTAKE.md``.

    from home_jarvis.intake import Sorter
    sorter = Sorter(Path("/vault"), config_path=config_path(), classifier=...)
    plan = sorter.scan(apply=False)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .classify import EngineClassifier, NoClassifier, ScriptedClassifier, demo_classifier
from .config import IntakeConfig, ListSpec, config_path, load_path
from .sorter import DecideError, Sorter

__all__ = [
    "DecideError",
    "EngineClassifier",
    "IntakeConfig",
    "ListSpec",
    "NoClassifier",
    "ScriptedClassifier",
    "Sorter",
    "build_sorter",
    "config_path",
    "demo_classifier",
    "load_path",
]


def demo_mode() -> bool:
    return bool(os.environ.get("HOME_JARVIS_DEMO") or os.environ.get("HUD"))


def build_sorter(app: Any, log: Any = None) -> Sorter:
    """The sorter for a running server: vault, intake.toml, engine, speech backend."""
    path = config_path()
    load_path(path)  # fail early on a broken intake.toml
    sorter = Sorter(
        Path(os.environ.get("OPENJARVIS_VAULT", "/vault")),
        config_path=path,
        log=log,
        speech=lambda: getattr(app.state, "speech_backend", None),
    )
    if demo_mode():
        sorter.classifier = demo_classifier()
    else:
        server_model = getattr(app.state, "model", "") or ""
        sorter.classifier = EngineClassifier(
            app.state.engine, lambda: sorter.config.classifier_model or server_model
        )
    return sorter
