"""Command line for the intake sorter (the same code the HUD calls).

    python -m home_jarvis.intake scan            # dry run: print the plan
    python -m home_jarvis.intake scan --apply    # file it
    python -m home_jarvis.intake state
    python -m home_jarvis.intake decide ID file|add_list|edit|leave \\
        [--envelope JSON] [--list JSON]
    python -m home_jarvis.intake log [-n 20]

Options: ``--vault`` (default ``$OPENJARVIS_VAULT`` or /vault), ``--config``
(default the intake.toml in /data/custom), ``--demo`` (keyword classifier, no
model), ``--no-model`` (notes escalate, rules still file chats). Without
those, the classifier uses the OpenJarvis engine from the server config.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
from pathlib import Path
from typing import Any

from .classify import EngineClassifier, NoClassifier, demo_classifier
from .config import config_path
from .sorter import DecideError, Sorter


def _engine_classifier(sorter: Sorter) -> Any:
    """The configured OpenJarvis engine, or None when it cannot be reached."""
    try:
        from openjarvis.core.config import load_config
        from openjarvis.core.credentials import inject_credentials
        from openjarvis.engine import get_engine
        from openjarvis.intelligence import register_builtin_models

        inject_credentials()
        config = load_config()
        register_builtin_models()
        server_model = config.server.model or config.intelligence.default_model or ""
        model = sorter.config.classifier_model or server_model
        resolved = get_engine(config, model=model or None)
    except Exception as exc:  # noqa: BLE001
        print(f"no engine ({exc}); notes will escalate", file=sys.stderr)
        return None
    if resolved is None:
        print("no engine available; notes will escalate", file=sys.stderr)
        return None
    return EngineClassifier(resolved[1], lambda: sorter.config.classifier_model or server_model)


@functools.cache
def _speech() -> Any:
    try:
        from openjarvis.core.config import load_config
        from openjarvis.speech._discovery import get_speech_backend

        return get_speech_backend(load_config())
    except Exception:  # noqa: BLE001  (no speech extra: audio escalates)
        return None


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m home_jarvis.intake")
    parser.add_argument("--vault", default=os.environ.get("OPENJARVIS_VAULT", "/vault"))
    parser.add_argument("--config", default=None, help="intake.toml path")
    parser.add_argument("--demo", action="store_true", help="keyword classifier, no model")
    parser.add_argument("--no-model", action="store_true", help="never call a model")
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan", help="plan (dry run) or file the inbox")
    scan.add_argument("--apply", action="store_true")
    sub.add_parser("state", help="menu, inbox and pending escalations")
    decide = sub.add_parser("decide", help="act on one plan item")
    decide.add_argument("id")
    decide.add_argument("action", choices=["file", "add_list", "edit", "leave"])
    decide.add_argument("--envelope", default=None, help="JSON envelope (edit)")
    decide.add_argument("--list", dest="new_list", default=None, help="JSON list (add_list)")
    log = sub.add_parser("log", help="decision log tail")
    log.add_argument("-n", type=int, default=20)
    args = parser.parse_args(argv)

    sorter = Sorter(
        Path(args.vault),
        config_path=Path(args.config) if args.config else config_path(),
        speech=_speech,
    )
    needs_model = args.cmd in ("scan", "decide")
    if args.demo:
        sorter.classifier = demo_classifier()
    elif args.no_model or not needs_model:
        sorter.classifier = NoClassifier()
    else:
        sorter.classifier = _engine_classifier(sorter) or NoClassifier()

    if args.cmd == "scan":
        _print(sorter.scan(apply=True if args.apply else False, by="cli"))
    elif args.cmd == "state":
        _print(sorter.state())
    elif args.cmd == "log":
        _print(sorter.decision_log(args.n))
    else:
        try:
            envelope = json.loads(args.envelope) if args.envelope else None
            new_list = json.loads(args.new_list) if args.new_list else None
            _print(sorter.decide(args.id, args.action, envelope, new_list, by="cli"))
        except json.JSONDecodeError as exc:
            print(f"bad JSON: {exc}", file=sys.stderr)
            return 2
        except DecideError as exc:
            _print({"ok": False, "error": str(exc), "checks": exc.checks})
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
