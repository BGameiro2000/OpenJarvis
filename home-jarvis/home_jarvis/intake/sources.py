"""Detect what a file is by its content, and extract it into items.

A source file (one file in the inbox) becomes one or more ``Item``s. Each
format has one extractor, registered in ``EXTRACTORS`` by format name:

    note       .md, .markdown, .txt (and text with no extension)
    whatsapp   WhatsApp .txt export, Android or iOS layout
    telegram   Telegram result.json (one chat, or a full export)
    claude     Claude export conversations.json
    chatgpt    ChatGPT export conversations.json (message tree)
    zip        an archive, opened in memory with limits
    audio      m4a mp3 wav ogg opus webm flac
    unknown    anything else

Item kinds are fewer: ``note``, ``chat`` (with people), ``ai_chat``,
``audio`` and ``unknown``. Nothing here writes to disk or calls a model.
Every text read here is untrusted: it is data, never instructions.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

AUDIO_EXT = {"m4a", "mp3", "wav", "ogg", "opus", "webm", "flac"}
NOTE_EXT = {"md", "markdown", "txt", ""}
# Files that travel with a chat export and are not content of their own.
MEDIA_EXT = AUDIO_EXT | {
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp",
    "heic",
    "mp4",
    "mov",
    "3gp",
    "avi",
    "vcf",
    "pdf",
    "doc",
    "docx",
    "html",
    "htm",
    "css",
    "js",
    "tgs",
    "aac",
}

ZIP_MAX_MEMBERS = 2000
ZIP_MAX_TOTAL = 500 * 1024 * 1024

HUMAN = "Bernardo"
PLATFORM_NAMES = {"whatsapp": "WhatsApp", "telegram": "Telegram"}


class SourceError(ValueError):
    """A file that cannot be read safely (bad zip, limits, traversal)."""


@dataclass
class Message:
    time: str  # ISO 8601, seconds, no zone ("" if unknown)
    sender: str
    text: str

    @property
    def key(self) -> str:
        raw = f"{self.time}\x1f{self.sender}\x1f{self.text}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @property
    def day(self) -> str:
        return self.time[:10] or "undated"


@dataclass
class Item:
    kind: str  # note, chat, ai_chat, audio, unknown
    title: str
    text: str = ""
    source: str = ""  # inbox file name
    member: str = ""  # path inside a zip, if any
    fmt: str = ""  # the format that produced it (see module doc)
    source_key: str = ""
    participants: list[str] = field(default_factory=list)
    started: str = ""
    ended: str = ""
    messages: list[Message] = field(default_factory=list)
    platform: str = ""
    reason: str = ""  # preset escalation reason (audio, unknown)
    audio: bytes = b""  # raw audio, kept only until transcribed
    audio_ext: str = ""

    @property
    def count(self) -> int:
        return len(self.messages) or 1


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ext_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base.lstrip(".") else ""


def slugify(text: str, fallback: str = "item") -> str:
    """Lowercase ASCII words joined by hyphens, 1 to 64 chars."""
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")[:64].strip("-")
    return slug or fallback


# ---------------------------------------------------------------- detection

_WA_STAMP = (
    r"(\d{1,2})[/.](\d{1,2})[/.](\d{2,4}),? (\d{1,2}):(\d{2})(?::(\d{2}))?\s?([AaPp]\.?[Mm]\.?)?"
)
_WA_ANDROID = re.compile(rf"^{_WA_STAMP} [-\u2013] (.*)$")
_WA_IOS = re.compile(rf"^\[{_WA_STAMP}\] (.*)$")
_INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202c\ufeff"), None)


def _clean_line(line: str) -> str:
    return line.translate(_INVISIBLE).replace("\u202f", " ").replace("\u00a0", " ")


def _is_wa_header(line: str) -> bool:
    line = _clean_line(line)
    return bool(_WA_ANDROID.match(line) or _WA_IOS.match(line))


def _audio_magic(data: bytes) -> str:
    head = data[:16]
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if head.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if data[4:8] == b"ftyp" and data[8:11] in (b"M4A", b"mp4", b"iso", b"MP4"):
        return "m4a"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return ""


def _text(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _json_kind(value: Any) -> str:
    if isinstance(value, dict):
        if isinstance(value.get("messages"), list) and ("name" in value or "type" in value):
            return "telegram"
        chats = value.get("chats")
        if isinstance(chats, dict) and isinstance(chats.get("list"), list):
            return "telegram"
        return ""
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value[:20]):
        if any("chat_messages" in v for v in value[:20]):
            return "claude"
        if any("mapping" in v for v in value[:20]):
            return "chatgpt"
    return ""


def detect(name: str, data: bytes) -> str:
    """Format of ``data`` (see module doc), by content first, then extension."""
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    ext = ext_of(name)
    if ext in AUDIO_EXT or _audio_magic(data):
        return "audio"
    text = _text(data)
    if text is None:
        return "unknown"
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            kind = _json_kind(json.loads(stripped))
        except (json.JSONDecodeError, RecursionError):
            kind = ""
        else:
            if kind:
                return kind
            if ext == "json":
                return "unknown"
    lines = [ln for ln in text.splitlines()[:80] if ln.strip()]
    if lines and _is_wa_header(lines[0]) and sum(_is_wa_header(ln) for ln in lines) >= 2:
        return "whatsapp"
    if len(lines) == 1 and _is_wa_header(lines[0]) and ext == "txt":
        return "whatsapp"
    return "note" if ext in NOTE_EXT else "unknown"


# --------------------------------------------------------------- extractors


def _note(name: str, data: bytes, ctx: dict) -> list[Item]:
    text = (_text(data) or "").replace("\r\n", "\n").strip()
    body = text
    if body.startswith("---\n"):  # skip a YAML front matter block
        end = body.find("\n---", 4)
        body = body[end + 4 :] if end != -1 else body
    title = ""
    for line in body.splitlines():
        line = line.strip()
        if line:
            title = line.lstrip("#").strip()
            break
    title = (title or posixpath.basename(name).rsplit(".", 1)[0])[:120]
    key = "note:" + sha256(text.encode())[:16]
    return [Item(kind="note", title=title, text=text, fmt="note", source_key=key)]


def _wa_time(groups: tuple) -> str:
    d, m, y, hh, mm, ss, ampm = groups
    day, month, year = int(d), int(m), int(y)
    if year < 100:
        year += 2000
    hour = int(hh)
    if ampm:
        pm = ampm.lower().startswith("p")
        hour = hour % 12 + (12 if pm else 0)
    if month > 12 and day <= 12:  # month/day order
        day, month = month, day
    try:
        return datetime(year, month, day, hour, int(mm), int(ss or 0)).isoformat()
    except ValueError:
        return ""


def _wa_name(name: str, ctx: dict) -> str:
    for candidate in (posixpath.basename(name), ctx.get("zip", "")):
        stem = re.sub(r"\.(txt|zip)$", "", candidate, flags=re.I)
        m = re.match(r"^WhatsApp Chat (?:with|-|com|con|mit|avec)\s+(.+)$", stem, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _whatsapp(name: str, data: bytes, ctx: dict) -> list[Item]:
    text = (_text(data) or "").replace("\r\n", "\n")
    messages: list[Message] = []
    current: Message | None = None
    for raw in text.split("\n"):
        line = _clean_line(raw)
        m = _WA_ANDROID.match(line)
        ios = False
        if not m:
            m = _WA_IOS.match(line)
            ios = bool(m)
        if m:
            when = _wa_time(m.groups()[:7])
            rest = m.group(8)
            sender, sep, body = rest.partition(": ")
            if not sep:  # system line: "Messages and calls are end-to-end encrypted."
                current = None
                continue
            if ios and body.startswith(("Messages and calls are end-to-end", "Messages to this")):
                current = None
                continue
            current = Message(when, sender.strip(), body.strip())
            messages.append(current)
        elif current is not None:
            current.text += "\n" + line.rstrip()
    for msg in messages:
        msg.text = msg.text.strip()
    participants = sorted({m.sender for m in messages})
    chat = _wa_name(name, ctx) or ", ".join(participants) or "whatsapp chat"
    return [_chat_item("whatsapp", chat, messages, participants)]


def _chat_item(platform: str, chat: str, messages: list[Message], participants: list[str]) -> Item:
    times = [m.time for m in messages if m.time]
    return Item(
        kind="chat",
        title=f"{chat} ({PLATFORM_NAMES.get(platform, platform)})",
        fmt=platform,
        platform=platform,
        source_key=f"{platform}:{chat.lower()}",
        participants=participants,
        started=min(times) if times else "",
        ended=max(times) if times else "",
        messages=messages,
        text=render_chat(messages),
    )


def _tg_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return ""


def _tg_chat(chat: dict) -> Item | None:
    name = str(chat.get("name") or chat.get("id") or "telegram chat")
    messages = []
    for raw in chat.get("messages") or []:
        if not isinstance(raw, dict) or raw.get("type", "message") != "message":
            continue
        text = _tg_text(raw.get("text", "")).strip()
        if not text:
            continue
        when = str(raw.get("date", ""))[:19]
        messages.append(Message(when, str(raw.get("from") or "unknown"), text))
    if not messages:
        return None
    participants = sorted({m.sender for m in messages})
    return _chat_item("telegram", name, messages, participants)


def _telegram(name: str, data: bytes, ctx: dict) -> list[Item]:
    value = json.loads(_text(data) or "null")
    chats = value["chats"]["list"] if "chats" in value else [value]
    items = [_tg_chat(c) for c in chats if isinstance(c, dict)]
    return [i for i in items if i is not None]


def _iso(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return str(value or "")[:19]


def _ai_item(platform: str, conv_id: str, title: str, messages: list[Message]) -> Item:
    times = [m.time for m in messages if m.time]
    return Item(
        kind="ai_chat",
        title=title[:120] or "Untitled conversation",
        fmt=platform,
        platform=platform,
        source_key=f"{platform}:{conv_id}",
        participants=sorted({m.sender for m in messages}),
        started=min(times) if times else "",
        ended=max(times) if times else "",
        messages=messages,
        text=render_ai(messages),
    )


def _claude(name: str, data: bytes, ctx: dict) -> list[Item]:
    items = []
    for conv in json.loads(_text(data) or "[]"):
        if not isinstance(conv, dict):
            continue
        messages = []
        for raw in conv.get("chat_messages") or []:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            if not text:
                parts = [
                    str(c.get("text", ""))
                    for c in raw.get("content") or []
                    if isinstance(c, dict) and c.get("type", "text") == "text"
                ]
                text = "\n\n".join(p for p in parts if p).strip()
            if not text:
                continue
            who = HUMAN if raw.get("sender") == "human" else "Claude"
            messages.append(Message(_iso(raw.get("created_at")), who, text))
        if messages:
            conv_id = str(conv.get("uuid") or sha256(json.dumps(conv).encode())[:16])
            items.append(_ai_item("claude", conv_id, str(conv.get("name") or ""), messages))
    return items


def _chatgpt_path(mapping: dict, current: str | None) -> list[dict]:
    if current and current in mapping:  # walk up from the visible leaf
        path, node_id, seen = [], current, set()
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            path.append(mapping[node_id])
            node_id = mapping[node_id].get("parent")
        return list(reversed(path))
    roots = [n for n in mapping.values() if isinstance(n, dict) and not n.get("parent")]
    path, node, seen = [], roots[0] if roots else None, set()
    while isinstance(node, dict) and id(node) not in seen:
        seen.add(id(node))
        path.append(node)
        children = node.get("children") or []
        node = mapping.get(children[-1]) if children else None
    return path


def _chatgpt(name: str, data: bytes, ctx: dict) -> list[Item]:
    items = []
    for conv in json.loads(_text(data) or "[]"):
        if not isinstance(conv, dict) or not isinstance(conv.get("mapping"), dict):
            continue
        messages = []
        for node in _chatgpt_path(conv["mapping"], conv.get("current_node")):
            msg = node.get("message") if isinstance(node, dict) else None
            if not isinstance(msg, dict):
                continue
            role = (msg.get("author") or {}).get("role")
            if role not in ("user", "assistant"):
                continue
            parts = (msg.get("content") or {}).get("parts") or []
            text = "\n\n".join(p for p in parts if isinstance(p, str) and p.strip()).strip()
            if not text:
                continue
            who = HUMAN if role == "user" else "ChatGPT"
            messages.append(Message(_iso(msg.get("create_time")), who, text))
        if messages:
            conv_id = str(conv.get("id") or conv.get("conversation_id") or "")
            conv_id = conv_id or sha256(json.dumps(conv).encode())[:16]
            items.append(_ai_item("chatgpt", conv_id, str(conv.get("title") or ""), messages))
    return items


def _audio(name: str, data: bytes, ctx: dict) -> list[Item]:
    ext = ext_of(name)
    if ext not in AUDIO_EXT:
        ext = _audio_magic(data) or "wav"
    return [
        Item(
            kind="audio",
            title=posixpath.basename(name),
            fmt="audio",
            source_key="audio:" + sha256(data)[:16],
            reason="needs a transcript",
            audio=data,
            audio_ext=ext,
        )
    ]


def _unknown(name: str, data: bytes, ctx: dict) -> list[Item]:
    return [
        Item(
            kind="unknown",
            title=posixpath.basename(name),
            fmt="unknown",
            source_key="file:" + sha256(data)[:16],
            reason="unsupported format",
        )
    ]


def _safe_member(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name:
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in name.split("/")


def _zip(name: str, data: bytes, ctx: dict) -> list[Item]:
    depth = ctx.get("depth", 0)
    if depth >= 2:
        raise SourceError("zip nested more than one level")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceError(f"bad zip: {exc}") from exc
    infos = archive.infolist()
    if len(infos) > ZIP_MAX_MEMBERS:
        raise SourceError(f"zip has {len(infos)} members, the limit is {ZIP_MAX_MEMBERS}")
    total = sum(i.file_size for i in infos) + ctx.get("used", 0)
    if total > ZIP_MAX_TOTAL:
        raise SourceError(f"zip unpacks to {total} bytes, the limit is {ZIP_MAX_TOTAL}")
    for info in infos:
        if not _safe_member(info.filename):
            raise SourceError(f"zip member {info.filename!r} points outside the archive")
    budget = [ZIP_MAX_TOTAL - ctx.get("used", 0)]
    members: list[tuple[str, bytes, str]] = []
    for info in infos:
        mname = info.filename
        base = posixpath.basename(mname)
        if info.is_dir() or mname.startswith("__MACOSX/") or base.startswith(".") or not base:
            continue
        with archive.open(info) as fh:  # never trust the declared size alone
            blob = fh.read(min(info.file_size, budget[0]) + 1)
        if len(blob) > info.file_size or len(blob) > budget[0]:
            raise SourceError(f"zip member {mname!r} is larger than declared")
        budget[0] -= len(blob)
        members.append((mname, blob, detect(mname, blob)))
    chat_formats = {"whatsapp", "telegram", "claude", "chatgpt"}
    is_export = any(fmt in chat_formats for _, _, fmt in members)
    zip_name = ctx.get("zip") or posixpath.basename(name)
    items: list[Item] = []
    for mname, blob, fmt in members:
        if is_export and fmt not in chat_formats:
            continue  # media and companion files of a chat export
        sub = {"zip": zip_name, "depth": depth + 1, "used": ZIP_MAX_TOTAL - budget[0]}
        for item in extract_format(fmt, mname, blob, sub):
            item.member = f"{mname}/{item.member}" if item.member else mname
            items.append(item)
    return items


Extractor = Callable[[str, bytes, dict], list[Item]]

EXTRACTORS: dict[str, Extractor] = {
    "note": _note,
    "whatsapp": _whatsapp,
    "telegram": _telegram,
    "claude": _claude,
    "chatgpt": _chatgpt,
    "zip": _zip,
    "audio": _audio,
    "unknown": _unknown,
}


def extract_format(fmt: str, name: str, data: bytes, ctx: dict | None = None) -> list[Item]:
    return EXTRACTORS.get(fmt, _unknown)(name, data, ctx or {})


def extract(name: str, data: bytes) -> tuple[str, list[Item]]:
    """``(format, items)`` for one inbox file. Raises ``SourceError``."""
    fmt = detect(name, data)
    try:
        items = extract_format(fmt, name, data)
    except SourceError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"could not read {fmt}: {exc}") from exc
    for item in items:
        item.source = name
    return fmt, items


# ---------------------------------------------------------------- rendering


def render_chat(messages: list[Message], last_day: str = "") -> str:
    """Messages grouped by day; ``last_day`` suppresses a repeated header."""
    out: list[str] = []
    day = last_day
    for msg in messages:
        if msg.day != day:
            day = msg.day
            out.append(f"## {day}\n")
        clock = msg.time[11:16] if len(msg.time) >= 16 else ""
        who = f"{clock} {msg.sender}" if clock else msg.sender
        body = msg.text.replace("\n", "\n> ")
        out.append(f"**{who}**: {body}\n")
    return "\n".join(out).strip() + "\n" if out else ""


def render_ai(messages: list[Message]) -> str:
    out = []
    for msg in messages:
        when = msg.time.replace("T", " ")[:16]
        head = f"**{msg.sender}**" + (f", {when}" if when else "")
        out.append(f"{head}\n\n{msg.text}\n")
    return "\n".join(out)


def render_messages(item: Item, messages: list[Message], last_day: str = "") -> str:
    if item.kind == "ai_chat":
        return render_ai(messages)
    return render_chat(messages, last_day)
