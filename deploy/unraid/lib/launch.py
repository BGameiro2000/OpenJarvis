"""Custom code hook, then the normal ``jarvis`` CLI in the same process.

Registries in OpenJarvis fill at import time, so user modules must be imported
in the server process before ``serve`` builds the agent.

- ``cognee_tools`` (shipped in the image) is imported when ``COGNEE_URL`` is set.
- Every module named in ``OPENJARVIS_CUSTOM_MODULES`` (comma separated) is
  imported next. ``/data/custom`` is on ``PYTHONPATH``.

A module that fails to import is reported and skipped; the server still starts.
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback


def _log(msg: str) -> None:
    print(f"[openjarvis-unraid] {msg}", file=sys.stderr, flush=True)


def load_custom_modules() -> None:
    names: list[str] = []
    if os.environ.get("COGNEE_URL", "").strip():
        names.append("cognee_tools")
    for raw in os.environ.get("OPENJARVIS_CUSTOM_MODULES", "").split(","):
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    for name in names:
        try:
            importlib.import_module(name)
            _log(f"custom module loaded: {name}")
        except Exception:  # noqa: BLE001
            _log(f"custom module FAILED, skipped: {name}")
            traceback.print_exc()


def main() -> None:
    load_custom_modules()
    from openjarvis.cli import main as jarvis_main

    sys.argv = ["jarvis", *sys.argv[1:]]
    jarvis_main()


if __name__ == "__main__":
    main()
