"""Write or check the OpenJarvis config for the Unraid container.

--first-start PATH VAULT  Apply the Unraid settings to PATH (created by
                          ``jarvis init``, or from scratch if missing).
--check PATH VAULT        Never writes. Prints warnings for settings that
                          drifted from the Unraid defaults.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import tomlkit

SAFE_TOOLS = ["think", "calculator", "web_search", "memory_manage", "cognee_recall"]

# (env var, engine, default model). First key present wins.
PROVIDERS = [
    ("ANTHROPIC_API_KEY", "cloud", "claude-sonnet-4-6"),
    ("MISTRAL_API_KEY", "litellm", "mistral/mistral-medium-latest"),
    ("OPENAI_API_KEY", "cloud", "gpt-5"),
]


def log(msg: str) -> None:
    print(f"[openjarvis-unraid] {msg}", file=sys.stderr)


def pick_engine() -> tuple[str, str] | None:
    for env, engine, model in PROVIDERS:
        if os.environ.get(env, "").strip():
            return engine, model
    return None


def table(doc, *path: str):
    node = doc
    for key in path:
        if key not in node:
            node[key] = tomlkit.table()
        node = node[key]
    return node


def first_start(cfg_path: Path, vault: Path) -> None:
    if cfg_path.exists():
        doc = tomlkit.parse(cfg_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
        doc.add(
            tomlkit.comment("OpenJarvis configuration (written by openjarvis-unraid)")
        )

    choice = pick_engine()
    if choice is None:
        log(
            "WARNING: no ANTHROPIC_API_KEY, MISTRAL_API_KEY or OPENAI_API_KEY set; "
            "the server will refuse to start until one is set"
        )
        choice = PROVIDERS[0][1], PROVIDERS[0][2]
    engine, model = choice

    # Drop the local-engine block jarvis init wrote, then set a cloud default.
    eng = table(doc, "engine")
    for key in list(eng.keys()):
        if key != "default":
            del eng[key]
    eng["default"] = engine
    # uzu and vllm default to localhost:8000, which is this server. Without
    # this, discovery mistakes OpenJarvis itself for an inference engine.
    for local in ("uzu", "vllm"):
        t = tomlkit.table()
        t["host"] = "http://127.0.0.1:9"
        eng[local] = t

    intel = table(doc, "intelligence")
    intel["default_model"] = model
    intel["fallback_model"] = model

    server = table(doc, "server")
    server["host"] = "0.0.0.0"
    server["port"] = 8000

    tools = table(doc, "tools")
    tools["enabled"] = SAFE_TOOLS

    analytics = table(doc, "analytics")
    analytics["enabled"] = False
    analytics["choice_recorded"] = True

    skills = table(doc, "skills")
    skills["auto_sync"] = False
    if "sources" in skills:
        del skills["sources"]

    mf = table(doc, "memory_files")
    mf["soul_path"] = str(vault / "SOUL.md")
    mf["memory_path"] = str(vault / "MEMORY.md")
    mf["user_path"] = str(vault / "USER.md")

    # File tools (not enabled by default) would be limited to the vault.
    table(doc, "security")["allowed_dirs"] = [str(vault)]

    cfg_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    log(f"config written: engine={engine} model={model} tools={','.join(SAFE_TOOLS)}")


def check(cfg_path: Path, vault: Path) -> None:
    try:
        doc = tomlkit.parse(cfg_path.read_text(encoding="utf-8")).unwrap()
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: cannot parse {cfg_path}: {exc}")
        return
    warn = []
    if doc.get("analytics", {}).get("enabled", False):
        warn.append("[analytics] enabled is true")
    skills = doc.get("skills", {})
    if skills.get("auto_sync", False) or skills.get("sources"):
        warn.append("[skills] auto_sync or sources is set")
    enabled = doc.get("tools", {}).get("enabled", [])
    if isinstance(enabled, str):
        enabled = [t.strip() for t in enabled.split(",")]
    risky = sorted(
        set(enabled)
        & {
            "shell_exec",
            "code_interpreter",
            "repl",
            "file_write",
            "http_request",
            "git_commit",
        }
    )
    if risky:
        warn.append(f"[tools] enabled includes {', '.join(risky)}")
    mf_path = doc.get("memory_files", {}).get("memory_path", "")
    if mf_path and not mf_path.startswith(str(vault)):
        warn.append(f"[memory_files] memory_path is outside {vault}")
    engines = doc.get("engine", {})
    for local in ("uzu", "vllm"):
        if engines.get(local, {}).get("host", "") in ("", "http://localhost:8000"):
            warn.append(
                f"[engine.{local}] host is unset; it defaults to this server "
                f'(add host = "http://127.0.0.1:9")'
            )
    for w in warn:
        log(f"WARNING: {w}")
    if pick_engine() is None:
        log("WARNING: no cloud provider key in env; the server will not start")


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("--first-start", "--check"):
        print(__doc__, file=sys.stderr)
        return 2
    cfg, vault = Path(argv[2]), Path(argv[3])
    if argv[1] == "--first-start":
        first_start(cfg, vault)
    else:
        check(cfg, vault)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
