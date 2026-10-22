"""The intake sorter: scan the inbox, plan, file, dedupe, decide escalations.

Everything works on a vault ``Path`` so tests use a temporary folder:

    inbox/                      what comes in (only top level files are read)
    inbox/archive/YYYY-MM/      originals after filing
    inbox/.decision-log.jsonl   one line per source file decision
    inbox/.intake/conversations.json
                                what is filed, by source_key (message keys for
                                chats, so a newer export only adds new messages)

A scan in dry run computes the plan and writes nothing at all. Apply files
what the plan says. Escalations stay in the inbox until someone decides.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .classify import Classifier, NoClassifier
from .config import (
    DEFAULT_FOLDERS,
    ROUTES,
    ConfigError,
    IntakeConfig,
    ListSpec,
    load_path,
    persist_list,
)
from .sources import (
    Item,
    Message,
    SourceError,
    detect,
    extract,
    render_messages,
    sha256,
    slugify,
)
from .validate import failures, normalise, passed, validate

SKIP_NAMES = {"AGENTS.md"}
MAX_CAPTURE = 200 * 1024 * 1024
LOG_NAME = ".decision-log.jsonl"
STATE_DIR = ".intake"
INDEX_NAME = "conversations.json"
PREVIEW = 400

RULE_WHY = {
    "chat": "chat with people, filed by rule and never sent to the model",
    "ai_chat": "conversation with an AI assistant, filed by rule",
}
RULE_LIST = {"chat": "chats.md", "ai_chat": "ai-chats.md"}
NOTE_SUBDIR = {"chat": "chats", "ai_chat": "ai-chats"}


class DecideError(ValueError):
    """A decision that cannot be carried out (unknown id, bad list, bad envelope)."""

    def __init__(self, message: str, checks: list[dict] | None = None, status: int = 400):
        super().__init__(message)
        self.checks = checks or []
        self.status = status


@dataclass
class Planned:
    """One item of the plan, with what apply needs that JSON does not carry."""

    item: Item
    out: dict[str, Any]
    new_messages: list[Message] = field(default_factory=list)
    index_entry: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourcePlan:
    path: Path
    name: str
    sha: str
    fmt: str
    planned: list[Planned]
    archive: str = ""  # vault relative, set when the whole source can move

    @property
    def id(self) -> str:
        return self.sha[:12]


def _json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _link(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _one_line(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


class Sorter:
    def __init__(
        self,
        vault: Path,
        *,
        config: IntakeConfig | None = None,
        config_path: Path | None = None,
        classifier: Classifier | None = None,
        log: Any = None,
        speech: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.vault = Path(vault)
        self.config_path = config_path
        self._config = config
        self.classifier = classifier or NoClassifier()
        self.log = log
        self.speech = speech  # a speech backend, or a function returning one (or None)
        self.clock = clock
        self._lock = threading.RLock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._answers: dict[str, dict[str, Any]] = {}  # item id -> raw classifier answer
        self._transcripts: dict[str, str] = {}
        self._probe: dict[tuple, tuple[str, str]] = {}
        self.last_scan: dict[str, Any] | None = None

    # ------------------------------------------------------------ basics

    @property
    def inbox(self) -> Path:
        return self.vault / "inbox"

    @property
    def config(self) -> IntakeConfig:
        if self._config is not None:
            return self._config
        if self.config_path is not None:
            return load_path(self.config_path)
        return IntakeConfig()

    def _vpath(self, rel: str) -> Path:
        """``rel`` inside the vault, or ValueError (symlinks resolved)."""
        root = self.vault.resolve()
        path = (root / rel).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"path {rel!r} leaves the vault")
        return path

    def _now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.clock()))

    def _emit(self, kind: str, text: str = "", **data: Any) -> None:
        if self.log is None:
            return
        from ..events import FloorEvent

        self.log.emit(FloorEvent(kind=kind, text=text, source="intake", data=data))

    def _backend(self) -> Any:
        backend = self.speech
        if callable(backend) and not hasattr(backend, "transcribe"):
            backend = backend()
        return backend if hasattr(backend, "transcribe") else None

    def transcription(self) -> dict[str, Any]:
        backend = self._backend()
        name = ""
        if backend is not None:
            name = str(getattr(backend, "backend_id", "") or type(backend).__name__)
        return {"available": backend is not None, "backend": name}

    # ------------------------------------------------------------ inbox

    def inbox_files(self) -> list[Path]:
        if not self.inbox.is_dir():
            return []
        files = []
        for path in sorted(self.inbox.iterdir()):
            if path.name in SKIP_NAMES or path.name.startswith("."):
                continue
            if path.is_symlink() or not path.is_file():
                continue  # archive/, .intake/, folders, links out of the vault
            files.append(path)
        return files

    def _probe_file(self, path: Path) -> tuple[str, str, int, float]:
        st = path.stat()
        key = (path.name, st.st_size, st.st_mtime)
        if key not in self._probe:
            data = path.read_bytes()
            self._probe[key] = (sha256(data), detect(path.name, data))
        sha, kind = self._probe[key]
        return sha, kind, st.st_size, st.st_mtime

    def list_inbox(self) -> list[dict[str, Any]]:
        out = []
        for path in self.inbox_files():
            try:
                sha, kind, size, mtime = self._probe_file(path)
            except OSError:
                continue
            out.append(
                {"name": path.name, "size": size, "mtime": mtime, "kind": kind, "sha256": sha}
            )
        return out

    # ------------------------------------------------------------ records

    def _log_lines(self) -> list[dict[str, Any]]:
        path = self.inbox / LOG_NAME
        if not path.is_file():
            return []
        lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                lines.append(json.loads(raw))
            except ValueError:
                continue
        return lines

    def _filed_shas(self) -> set[str]:
        done = {"filed", "merged", "skipped"}
        return {
            line.get("sha256", "")
            for line in self._log_lines()
            if line.get("decision") in done and not line.get("dry_run")
        }

    def _index(self) -> dict[str, Any]:
        data = _json(self.inbox / STATE_DIR / INDEX_NAME, {})
        return data if isinstance(data, dict) else {}

    def _save_index(self, index: dict[str, Any]) -> None:
        path = self.inbox / STATE_DIR / INDEX_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _write_log(self, line: dict[str, Any]) -> None:
        path = self.inbox / LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ planning

    def _unique(self, rel: str, reserved: set[str]) -> str:
        """``rel``, or ``rel`` with -2, -3, ... before the extension: never overwrite."""
        stem, dot, ext = rel.rpartition(".")
        if not dot or "/" in ext:
            stem, ext = rel, ""
        candidate, n = rel, 1
        while candidate in reserved or self._vpath(candidate).exists():
            n += 1
            candidate = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
        reserved.add(candidate)
        return candidate

    def _transcribe(self, item: Item) -> str | None:
        key = sha256(item.audio)
        if key in self._transcripts:
            return self._transcripts[key]
        backend = self._backend()
        if backend is None:
            return None
        result = backend.transcribe(item.audio, format=item.audio_ext)
        text = getattr(result, "text", result)
        if isinstance(text, dict):
            text = text.get("text", "")
        text = str(text or "").strip()
        self._transcripts[key] = text
        return text

    def _rule(self, item: Item) -> dict[str, Any]:
        return {
            "route": "reference",
            "slug": slugify(item.title, item.kind.replace("_", "-")),
            "confidence": 1.0,
            "tags": [item.kind.replace("_", "-"), item.platform] if item.platform else [item.kind],
            "target_list": RULE_LIST[item.kind],
            "why": RULE_WHY[item.kind],
        }

    def _route(self, pid: str, item: Item, cfg: IntakeConfig) -> tuple[dict, str, list, str]:
        """(envelope, decided_by, checks, reason); reason non-empty means escalate."""
        if item.kind == "unknown":
            return self._escalation(item.reason), "detect", [], item.reason
        if item.kind == "audio" and not item.text:
            try:
                text = self._transcribe(item)
            except Exception as exc:  # noqa: BLE001  (a backend error is an escalation)
                reason = f"transcription failed: {exc}"
                return self._escalation(reason), "speech", [], reason
            if not text:
                reason = item.reason if text is None else "empty transcript"
                return self._escalation(reason), "speech", [], reason
            item.text = text
        if item.kind == "chat" or (item.kind == "ai_chat" and not cfg.classify_ai_chats):
            decided_by = f"rule:{item.kind}"
            raw = self._rule(item)
        else:
            decided_by = "model"
            text = item.text
            if item.kind == "ai_chat":  # title and first turn only
                text = render_messages(item, item.messages[:2])
            if pid in self._answers:
                raw = self._answers[pid]
            else:
                try:
                    raw = self.classifier.classify(item.title, text, cfg.lists)
                except Exception as exc:  # noqa: BLE001
                    reason = f"classifier failed: {exc}"
                    check = {"name": "classifier answered", "ok": False, "detail": str(exc)}
                    return self._escalation(reason), decided_by, [check], reason
                self._answers[pid] = raw
        envelope = normalise(raw)
        checks = validate(envelope, cfg)
        return envelope, decided_by, checks, "" if passed(checks) else failures(checks)

    @staticmethod
    def _escalation(reason: str) -> dict[str, Any]:
        return {
            "route": "escalate",
            "slug": "",
            "confidence": 0.0,
            "tags": [],
            "target_list": "",
            "why": reason,
        }

    def _note_rel(self, item: Item, spec: ListSpec, slug: str, reserved: set[str]) -> str:
        folder = spec.folder
        if item.kind in NOTE_SUBDIR:
            folder = f"{folder}/{NOTE_SUBDIR[item.kind]}"
        return self._unique(f"{folder}/{slug}.md", reserved)

    def _out(self, pid: str, name: str, fmt: str, item: Item) -> dict[str, Any]:
        out = {
            "id": pid,
            "source": name,
            "kind": item.kind,
            "format": fmt,
            "title": item.title,
            "count": item.count,
            "preview": item.text[:PREVIEW],
            "decided_by": "",
            "envelope": self._escalation(""),
            "checks": [],
            "decision": "escalate",
            "actions": [],
            "reason": "",
        }
        if item.member:
            out["member"] = item.member
        return out

    def _plan_source(
        self,
        path: Path,
        data: bytes,
        cfg: IntakeConfig,
        reserved: set[str],
        index: dict[str, Any],
        filed: set[str],
    ) -> SourcePlan:
        name = path.name
        sha = sha256(data)
        sid = sha[:12]
        fmt = detect(name, data)
        plan = SourcePlan(path, name, sha, fmt, [])
        if sha in filed:
            item = Item(kind=fmt if fmt in ("note", "audio") else "unknown", title=name)
            out = self._out(f"{sid}-0000", name, fmt, item)
            out.update(
                decided_by="dedupe:sha256",
                decision="skip",
                reason="already filed (same sha256)",
                envelope={**self._escalation("already filed"), "route": ""},
            )
            plan.planned.append(Planned(item, out))
        else:
            try:
                fmt, items = extract(name, data)
                plan.fmt = fmt
                if not items:
                    raise SourceError("nothing to extract")
            except SourceError as exc:
                item = Item(kind="unknown", title=name, reason=str(exc))
                items = [item]
            for idx, item in enumerate(items):
                item.source = name
                pid = f"{sid}-{idx:04d}"
                plan.planned.append(
                    self._plan_item(pid, name, plan.fmt, item, cfg, reserved, index)
                )
        if all(p.out["decision"] != "escalate" for p in plan.planned):
            month = time.strftime("%Y-%m", time.localtime(self.clock()))
            plan.archive = self._unique(f"inbox/archive/{month}/{name}", reserved)
            for p in plan.planned:
                p.out["actions"].append({"op": "move", "path": plan.archive})
        self._links(plan)
        return plan

    def _plan_item(
        self,
        pid: str,
        name: str,
        fmt: str,
        item: Item,
        cfg: IntakeConfig,
        reserved: set[str],
        index: dict[str, Any],
    ) -> Planned:
        out = self._out(pid, name, fmt, item)
        planned = Planned(item, out)
        known = index.get(item.source_key) if item.source_key else None
        if known:
            out["decided_by"] = "dedupe:source_key"
            out["envelope"] = {
                "route": known.get("route", ""),
                "slug": Path(known.get("path", "")).stem,
                "confidence": 1.0,
                "tags": [],
                "target_list": known.get("target_list", ""),
                "why": "already filed",
            }
            keys = set(known.get("keys") or [])
            new = [m for m in item.messages if m.key not in keys]
            if item.messages and new:
                out.update(decision="merge", count=len(new))
                out["reason"] = f"{len(new)} new messages for {known.get('path', '')}"
                out["actions"].append({"op": "append", "path": known.get("path", "")})
                planned.new_messages = new
            else:
                out.update(decision="skip", reason="already filed")
            return planned

        envelope, decided_by, checks, reason = self._route(pid, item, cfg)
        out.update(envelope=envelope, decided_by=decided_by, checks=checks)
        out["preview"] = item.text[:PREVIEW]
        if reason:
            out.update(decision="escalate", reason=reason)
            return planned
        out["decision"] = "file"
        self._file_actions(planned, cfg, reserved)
        return planned

    def _file_actions(self, planned: Planned, cfg: IntakeConfig, reserved: set[str]) -> None:
        item, out = planned.item, planned.out
        envelope = out["envelope"]
        spec = cfg.find(envelope["target_list"])
        assert spec is not None  # validated
        if item.kind != "note" or spec.route != "gtd_action":
            note = self._note_rel(item, spec, envelope["slug"], reserved)
            out["actions"].append({"op": "write", "path": note})
        out["actions"].append({"op": "append", "path": spec.path})

    def _links(self, plan: SourcePlan) -> None:
        """Set the list line of each filed item, now that the archive path is known."""
        original = plan.archive or f"inbox/{plan.name}"
        for p in plan.planned:
            if p.out["decision"] != "file":
                continue
            env = p.out["envelope"]
            write = next((a["path"] for a in p.out["actions"] if a["op"] == "write"), "")
            if env["route"] == "gtd_action":
                target = _link(write or original)
                line = f"- [ ] {_one_line(p.item.title)} ([[{target}]])"
            else:
                line = f"- [[{Path(write).stem}]] {_one_line(env['why'])}"
            for action in p.out["actions"]:
                if action["op"] == "append":
                    action["line"] = line

    # ------------------------------------------------------------ scan

    def plan(self, cfg: IntakeConfig | None = None) -> list[SourcePlan]:
        cfg = cfg or self.config
        reserved: set[str] = set()
        index = self._index()
        filed = self._filed_shas()
        plans = []
        for path in self.inbox_files():
            try:
                data = path.read_bytes()
            except OSError:
                continue
            plans.append(self._plan_source(path, data, cfg, reserved, index, filed))
        return plans

    def scan(self, apply: bool | None = None, *, by: str = "cli") -> dict[str, Any]:
        """Plan every inbox file; with ``apply`` (default: not dry_run) file them."""
        with self._lock:
            cfg = self.config
            do_apply = (not cfg.dry_run) if apply is None else bool(apply)
            plans = self.plan(cfg)
            at = self.clock()
            if do_apply:
                logged = {(line.get("sha256"), line.get("decision")) for line in self._log_lines()}
                for plan in plans:
                    self._apply_source(plan, by=by, logged=logged)
            items = [p.out for plan in plans for p in plan.planned]
            summary: dict[str, int] = {}
            for out in items:
                key = out["envelope"]["route"] if out["decision"] in ("file", "merge") else ""
                key = key or ("escalate" if out["decision"] == "escalate" else "skip")
                summary[key] = summary.get(key, 0) + 1
            for plan in plans:
                self._announce(plan, dry_run=not do_apply)
            self._pending = {o["id"]: o for o in items if o["decision"] == "escalate"}
            self._answers = {k: v for k, v in self._answers.items() if k in self._pending}
            self.last_scan = {"at": at, "dry_run": not do_apply, "summary": summary}
            return {"dry_run": not do_apply, "at": at, "summary": summary, "items": items}

    def _announce(self, plan: SourcePlan, *, dry_run: bool) -> None:
        groups: dict[tuple[str, str], list[dict]] = {}
        for p in plan.planned:
            out = p.out
            if out["decision"] == "escalate":
                self._emit(
                    "escalate",
                    f"{plan.name}: {out['reason']}",
                    id=out["id"],
                    reason=out["reason"],
                    checks=out["checks"],
                    source=plan.name,
                    dry_run=dry_run,
                )
            elif out["decision"] in ("file", "merge"):
                env = out["envelope"]
                groups.setdefault((env["route"], env["target_list"]), []).append(out)
        for (route, target), outs in groups.items():
            self._emit(
                "route",
                f"{plan.name} -> {target}",
                route=route,
                target_list=target,
                confidence=min(o["envelope"]["confidence"] for o in outs),
                source=plan.name,
                items=len(outs),
                decided_by=outs[0]["decided_by"],
                dry_run=dry_run,
            )

    # ------------------------------------------------------------ apply

    def _note_text(self, item: Item, env: dict[str, Any], original: str) -> str:
        meta = {
            "title": item.title,
            "kind": item.kind,
            "platform": item.platform,
            "source": f"{item.source}/{item.member}" if item.member else item.source,
            "source_key": item.source_key,
            "participants": item.participants,
            "started": item.started,
            "ended": item.ended,
            "tags": env.get("tags") or [],
            "route": env.get("route", ""),
            "original": f"[[{_link(original)}]]",
            "filed": self._now_iso(),
        }
        head = ["---"]
        head += [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items() if v]
        head.append("---")
        body = item.text or f"Original file: [[{_link(original)}]]"
        return "\n".join(head) + f"\n\n# {item.title}\n\n{body.rstrip()}\n"

    def _write_new(self, rel: str, text: str) -> str:
        """Create ``rel`` without overwriting; returns the path actually written."""
        reserved: set[str] = set()
        while True:
            path = self._vpath(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8") as fh:
                    fh.write(text)
                return rel
            except FileExistsError:
                rel = self._unique(rel, reserved)

    def _append(self, rel: str, line: str) -> None:
        path = self._vpath(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if not path.exists():
            prefix = f"# {path.stem}\n\n"
        elif path.stat().st_size and not path.read_bytes().endswith(b"\n"):
            prefix = "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + line.rstrip("\n") + "\n")

    def _apply_item(self, plan: SourcePlan, p: Planned, index: dict[str, Any]) -> list[str]:
        """Carry out one planned file or merge; returns the vault paths touched."""
        out, item = p.out, p.item
        original = plan.archive or f"inbox/{plan.name}"
        touched: list[str] = []
        if out["decision"] == "file":
            env = out["envelope"]
            note = ""
            for action in out["actions"]:
                if action["op"] == "write":
                    note = self._write_new(action["path"], self._note_text(item, env, original))
                    if note != action["path"]:  # a file appeared since planning
                        action["path"] = note
                        self._links(plan)
                    touched.append(note)
            for action in out["actions"]:
                if action["op"] == "append":
                    self._append(action["path"], action["line"])
                    touched.append(action["path"])
            if item.source_key:
                index[item.source_key] = {
                    "path": note or original,
                    "keys": [m.key for m in item.messages],
                    "last_day": item.messages[-1].day if item.messages else "",
                    "kind": item.kind,
                    "route": env["route"],
                    "target_list": env["target_list"],
                    "title": item.title,
                }
            for path in touched:
                self._emit("file", path, source=plan.name, id=out["id"])
        elif out["decision"] == "merge":
            entry = index[item.source_key]
            rel = entry["path"]
            text = render_messages(item, p.new_messages, entry.get("last_day", ""))
            self._append(rel, "\n" + text)
            entry["keys"] = list(entry.get("keys") or []) + [m.key for m in p.new_messages]
            entry["last_day"] = p.new_messages[-1].day
            touched.append(rel)
            self._emit("file", rel, source=plan.name, id=out["id"], merged=len(p.new_messages))
        return touched

    def _move(self, plan: SourcePlan) -> None:
        dest = self._vpath(plan.archive)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            plan.archive = self._unique(plan.archive, set())
            dest = self._vpath(plan.archive)
        os.replace(plan.path, dest)
        self._emit("file", plan.archive, source=plan.name, op="move")

    def _apply_source(
        self, plan: SourcePlan, *, by: str, logged: set, only: set[str] | None = None
    ) -> dict[str, Any] | None:
        index = self._index()
        touched: list[str] = []
        chosen = [p for p in plan.planned if only is None or p.out["id"] in only]
        for p in chosen:
            touched += self._apply_item(plan, p, index)
        if any(p.out["decision"] in ("file", "merge") for p in chosen):
            self._save_index(index)
        if plan.archive:
            self._move(plan)
        decisions = {p.out["decision"] for p in plan.planned}
        acted = {p.out["decision"] for p in chosen}
        if "escalate" in decisions:
            decision = "partial" if acted & {"file", "merge"} else "escalated"
        elif "file" in acted:
            decision = "filed"
        elif "merge" in acted:
            decision = "merged"
        else:
            decision = "skipped"
        if (plan.sha, decision) in logged and decision == "escalated":
            return None  # an escalation is logged once, not on every scan
        lead = next(
            (p.out for p in chosen if p.out["decision"] in ("file", "merge")),
            chosen[0].out if chosen else plan.planned[0].out,
        )
        env = lead["envelope"]
        line = {
            "ts": self._now_iso(),
            "id": plan.id,
            "source": plan.name,
            "sha256": plan.sha,
            "kind": lead["kind"],
            "items": len(plan.planned),
            "decision": decision,
            "route": env.get("route", ""),
            "target_list": env.get("target_list", ""),
            "confidence": env.get("confidence", 0.0),
            "decided_by": lead["decided_by"],
            "dry_run": False,
            "by": by,
            "paths": [p for p in touched if not p.startswith("inbox/archive/")],
            "why": env.get("why", "") or lead["reason"],
        }
        if plan.archive:
            line["archived"] = plan.archive
        self._write_log(line)
        logged.add((plan.sha, decision))
        return line

    # ------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        cfg = self.config
        with self._lock:
            names = {f.name for f in self.inbox_files()}
            pending = [o for o in self._pending.values() if o["source"] in names]
        return {
            "dry_run": cfg.dry_run,
            "min_confidence": cfg.min_confidence,
            "routes": list(ROUTES),
            "menu": [s.to_json() for s in cfg.lists],
            "inbox": self.list_inbox(),
            "pending": pending,
            "transcription": self.transcription(),
            "last_scan": self.last_scan,
        }

    def decision_log(self, n: int = 50) -> list[dict[str, Any]]:
        return self._log_lines()[-n:]

    # ------------------------------------------------------------ capture

    def _safe_name(self, name: str) -> str:
        base = re.split(r"[\\/]", name or "")[-1]
        base = re.sub(r"[\x00-\x1f\x7f]", "", base)
        base = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", base).strip(" .")
        base = base.lstrip(".")[:120].strip() or "upload"
        if base in SKIP_NAMES:
            base = f"upload-{base}"
        return base

    def capture_bytes(self, name: str, data: bytes) -> str:
        """Save an upload in the inbox under a safe, new name; returns its vault path."""
        if len(data) > MAX_CAPTURE:
            raise ValueError("upload is larger than 200 MB")
        with self._lock:
            self.inbox.mkdir(parents=True, exist_ok=True)
            rel = self._unique(f"inbox/{self._safe_name(name)}", set())
            path = self._vpath(rel)
            if path.parent != self.inbox.resolve():
                raise ValueError("upload would land outside the inbox")
            with path.open("xb") as fh:
                fh.write(data)
            return rel

    def capture_text(self, text: str, why: str = "") -> str:
        if not text.strip():
            raise ValueError("empty text")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        body = text.strip() + "\n"
        if why.strip():
            body = f"---\ncaptured_why: {json.dumps(_one_line(why, 300))}\n---\n\n" + body
        return self.capture_bytes(f"note-{stamp}.md", body.encode("utf-8"))

    # ------------------------------------------------------------ decide

    def _find(self, item_id: str) -> tuple[SourcePlan, Planned]:
        if not re.match(r"^[0-9a-f]{12}-\d{4}$", item_id or ""):
            raise DecideError(f"bad item id {item_id!r}", status=404)
        cfg = self.config
        reserved: set[str] = set()
        index = self._index()
        for path in self.inbox_files():
            sha, _, _, _ = self._probe_file(path)
            if sha[:12] != item_id[:12]:
                continue
            plan = self._plan_source(path, path.read_bytes(), cfg, reserved, index, set())
            for p in plan.planned:
                if p.out["id"] == item_id:
                    return plan, p
        raise DecideError(f"no inbox item {item_id!r}", status=404)

    def decide(
        self,
        item_id: str,
        action: str,
        envelope: dict[str, Any] | None = None,
        new_list: dict[str, Any] | None = None,
        *,
        by: str = "hud",
    ) -> dict[str, Any]:
        """Act on one plan item: ``file``, ``add_list``, ``edit`` or ``leave``.

        A decision is a person's explicit choice, so it is carried out even
        when the config says dry run. The confidence floor does not apply to
        it; every other check does.
        """
        if action not in ("file", "add_list", "edit", "leave"):
            raise DecideError("action must be file, add_list, edit or leave")
        with self._lock:
            plan, p = self._find(item_id)
            out = p.out
            if action == "leave":
                self._pending.pop(item_id, None)
                line = {
                    "ts": self._now_iso(),
                    "id": plan.id,
                    "source": plan.name,
                    "sha256": plan.sha,
                    "kind": out["kind"],
                    "items": len(plan.planned),
                    "decision": "left",
                    "dry_run": False,
                    "by": by,
                    "item": item_id,
                    "why": out["reason"],
                }
                self._write_log(line)
                return {"ok": True, "decision": "left", "item": out, "log": line}
            if out["decision"] in ("skip", "merge"):
                raise DecideError(f"item {item_id} is already filed ({out['decision']})")
            cfg = self.config
            if action == "edit" and not envelope:
                raise DecideError("edit needs an envelope")
            env = normalise(envelope or out["envelope"])
            spec = None
            base = cfg
            if action == "add_list":
                cfg, spec = self._new_list(cfg, new_list or {}, env)
                env.update(target_list=spec.list, route=spec.route)
            checks = validate(env, cfg)
            for check in checks:
                if check["name"] == "confidence" and not check["ok"]:
                    check.update(ok=True, detail="accepted by hand")
            if not passed(checks):
                out.update(envelope=env, checks=checks)
                raise DecideError(failures(checks), checks, status=422)
            if spec is not None:
                self._commit_list(base, spec)
            # Re-plan this item with the chosen envelope and file it.
            out.update(envelope=env, checks=checks, decision="file", reason="", actions=[])
            out["decided_by"] = f"hand:{action}"
            reserved: set[str] = set()
            self._file_actions(p, cfg, reserved)
            # The original moves only when nothing else in it waits for filing.
            others = [q for q in plan.planned if q is not p]
            plan.archive = ""
            if not any(q.out["decision"] in ("escalate", "file") for q in others):
                month = time.strftime("%Y-%m", time.localtime(self.clock()))
                plan.archive = self._unique(f"inbox/archive/{month}/{plan.name}", reserved)
                out["actions"].append({"op": "move", "path": plan.archive})
            self._links(plan)
            line = self._apply_source(plan, by=by, logged=set(), only={item_id})
            self._pending.pop(item_id, None)
            self._emit(
                "route",
                f"{plan.name} -> {env['target_list']}",
                route=env["route"],
                target_list=env["target_list"],
                confidence=env["confidence"],
                source=plan.name,
                items=1,
                decided_by=out["decided_by"],
                dry_run=False,
            )
            return {"ok": True, "decision": "filed", "item": out, "log": line}

    def _new_list(
        self, cfg: IntakeConfig, raw: dict[str, Any], env: dict[str, Any]
    ) -> tuple[IntakeConfig, ListSpec]:
        """The menu with the requested list added (nothing is written yet)."""
        route = str(raw.get("route") or env.get("route") or "")
        spec = ListSpec(
            list=str(raw.get("name") or raw.get("list") or "").strip(),
            route=route,
            folder=str(raw.get("folder") or DEFAULT_FOLDERS.get(route, "")),
            scope=_one_line(str(raw.get("scope") or ""), 200),
        )
        try:
            return cfg.with_list(spec), spec
        except ConfigError as exc:
            raise DecideError(str(exc)) from exc

    def _commit_list(self, cfg: IntakeConfig, spec: ListSpec) -> None:
        """Write the new list to intake.toml (or keep it in memory) and create its file."""
        if self.config_path is not None and self._config is None:
            persist_list(self.config_path, cfg, spec)
        else:
            self._config = cfg.with_list(spec)
        if not self._vpath(spec.path).exists():
            self._append(spec.path, f"<!-- {spec.scope} -->" if spec.scope else "")
