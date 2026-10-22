"""Detection and extraction: every format, zip limits and traversal."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from home_jarvis.intake import sources
from home_jarvis.intake.sources import SourceError, detect, extract

ANDROID = """\
22/10/2026, 09:14 - Messages and calls are end-to-end encrypted.
22/10/2026, 09:14 - Ana Silva: Are you coming tonight?
I can pick you up
22/10/2026, 09:20 - Bernardo: Yes, at 8
23/10/2026, 18:02 - Ana Silva: <Media omitted>
"""

IOS = (
    "\u200e[22/10/26, 09:14:03] Ana Silva: \u200eMessages and calls are end-to-end encrypted.\n"
    "[22/10/26, 09:14:05] Ana Silva: Dinner on Friday?\n"
    "With the kids\n"
    "[22/10/26, 21:30:00] Bernardo: Sounds good\n"
)


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def claude_export() -> bytes:
    return json.dumps(
        [
            {
                "uuid": "c-1",
                "name": "GPU vs RAM",
                "created_at": "2026-10-20T10:00:00.000000Z",
                "chat_messages": [
                    {
                        "sender": "human",
                        "text": "Should I buy the A2000 or more RAM?",
                        "created_at": "2026-10-20T10:00:00.000000Z",
                        "content": [],
                    },
                    {
                        "sender": "assistant",
                        "text": "",
                        "created_at": "2026-10-20T10:00:09.000000Z",
                        "content": [{"type": "text", "text": "RAM first."}],
                    },
                ],
            },
            {"uuid": "c-2", "name": "", "chat_messages": []},
        ]
    ).encode()


def chatgpt_export() -> bytes:
    mapping = {
        "root": {"message": None, "parent": None, "children": ["u1"]},
        "u1": {
            "message": {
                "author": {"role": "user"},
                "content": {"parts": ["Plan a trip to Porto"]},
                "create_time": 1792620000.0,
            },
            "parent": "root",
            "children": ["a-old", "a1"],
        },
        "a-old": {
            "message": {
                "author": {"role": "assistant"},
                "content": {"parts": ["old branch"]},
                "create_time": 1792620005.0,
            },
            "parent": "u1",
            "children": [],
        },
        "a1": {
            "message": {
                "author": {"role": "assistant"},
                "content": {"parts": ["Day one: Ribeira.", {"image": "x"}]},
                "create_time": 1792620010.0,
            },
            "parent": "u1",
            "children": [],
        },
    }
    return json.dumps(
        [{"id": "g-1", "title": "Porto trip", "create_time": 1792620000.0, "mapping": mapping}]
    ).encode()


def telegram_export() -> bytes:
    return json.dumps(
        {
            "name": "Rui",
            "type": "personal_chat",
            "messages": [
                {"type": "service", "date": "2026-10-01T08:00:00", "action": "x"},
                {"date": "2026-10-01T08:01:00", "from": "Rui", "text": "Check this "},
                {
                    "date": "2026-10-01T08:02:00",
                    "from": "Rui",
                    "text": ["see ", {"type": "link", "text": "https://example.org"}, "!"],
                },
                {"date": "2026-10-01T08:03:00", "from": "Bernardo", "text": "ok"},
            ],
        }
    ).encode()


def test_detect_by_content():
    assert detect("a.md", b"# Title\nBuy milk") == "note"
    assert detect("a.txt", ANDROID.encode()) == "whatsapp"
    assert detect("_chat.txt", IOS.encode()) == "whatsapp"
    assert detect("result.json", telegram_export()) == "telegram"
    assert detect("conversations.json", claude_export()) == "claude"
    assert detect("conversations.json", chatgpt_export()) == "chatgpt"
    assert detect("x.zip", make_zip({"a.md": b"hi"})) == "zip"
    assert detect("memo.m4a", b"\x00\x00\x00\x20ftypM4A ") == "audio"
    assert detect("noext", b"ID3\x03\x00rest") == "audio"
    assert detect("weird.json", b'{"a": 1}') == "unknown"
    assert detect("doc.docx", b"\x00\x01binary") == "unknown"
    assert detect("todo.md", b"[ ] not json") == "note"


def test_note():
    fmt, items = extract("note.md", b"---\ntags: [x]\n---\n# Buy milk\n\nTomorrow")
    assert fmt == "note" and len(items) == 1
    assert items[0].kind == "note" and items[0].title == "Buy milk"
    assert items[0].source_key.startswith("note:") and items[0].source == "note.md"


def test_whatsapp_android_multiline_and_system_lines():
    _, [item] = extract("WhatsApp Chat with Ana Silva.txt", ANDROID.encode())
    assert item.kind == "chat" and item.platform == "whatsapp"
    assert item.source_key == "whatsapp:ana silva"
    assert [m.sender for m in item.messages] == ["Ana Silva", "Bernardo", "Ana Silva"]
    assert item.messages[0].text == "Are you coming tonight?\nI can pick you up"
    assert item.messages[0].time == "2026-10-22T09:14:00"
    assert item.started == "2026-10-22T09:14:00" and item.ended == "2026-10-23T18:02:00"
    assert "## 2026-10-23" in item.text


def test_whatsapp_ios_name_from_zip():
    data = make_zip({"_chat.txt": IOS.encode(), "IMG-0001.jpg": b"\xff\xd8", "a.opus": b"OggS"})
    _, items = extract("WhatsApp Chat - Ana Silva.zip", data)
    assert len(items) == 1  # media inside the export is ignored
    item = items[0]
    assert item.source_key == "whatsapp:ana silva" and item.member == "_chat.txt"
    assert [m.text for m in item.messages] == ["Dinner on Friday?\nWith the kids", "Sounds good"]
    assert item.messages[1].time == "2026-10-22T21:30:00"


def test_telegram():
    _, [item] = extract("result.json", telegram_export())
    assert item.kind == "chat" and item.source_key == "telegram:rui"
    assert [m.text for m in item.messages] == ["Check this", "see https://example.org!", "ok"]


def test_telegram_full_export():
    full = {"chats": {"list": [json.loads(telegram_export()), {"name": "Empty", "messages": []}]}}
    _, items = extract("result.json", json.dumps(full).encode())
    assert [i.title for i in items] == ["Rui (Telegram)"]


def test_claude_export():
    _, items = extract("conversations.json", claude_export())
    assert len(items) == 1  # the empty conversation is dropped
    item = items[0]
    assert item.kind == "ai_chat" and item.source_key == "claude:c-1" and item.title == "GPU vs RAM"
    assert [(m.sender, m.text) for m in item.messages] == [
        ("Bernardo", "Should I buy the A2000 or more RAM?"),
        ("Claude", "RAM first."),
    ]
    assert item.messages[0].time == "2026-10-20T10:00:00"


def test_chatgpt_export_follows_the_last_branch():
    _, [item] = extract("conversations.json", chatgpt_export())
    assert item.kind == "ai_chat" and item.source_key == "chatgpt:g-1"
    assert [m.text for m in item.messages] == ["Plan a trip to Porto", "Day one: Ribeira."]
    assert [m.sender for m in item.messages] == ["Bernardo", "ChatGPT"]


def test_claude_zip_ignores_companion_files():
    data = make_zip(
        {
            "conversations.json": claude_export(),
            "users.json": b'[{"uuid": "u"}]',
            "projects.json": b"[]",
        }
    )
    fmt, items = extract("claude-export.zip", data)
    assert fmt == "zip" and [i.source_key for i in items] == ["claude:c-1"]
    assert items[0].member == "conversations.json"


def test_zip_of_notes_and_one_nested_level():
    inner = make_zip({"deep.md": b"# Deep note"})
    data = make_zip({"a.md": b"# A", "sub/inner.zip": inner, ".DS_Store": b"x"})
    _, items = extract("notes.zip", data)
    assert sorted(i.title for i in items) == ["A", "Deep note"]
    deep = next(i for i in items if i.title == "Deep note")
    assert deep.member == "sub/inner.zip/deep.md"


def test_zip_nested_twice_is_rejected():
    level2 = make_zip({"x.md": b"x"})
    level1 = make_zip({"l2.zip": level2})
    with pytest.raises(SourceError, match="nested"):
        extract("outer.zip", make_zip({"l1.zip": level1}))


def test_zip_traversal_is_rejected():
    for bad in ("../evil.md", "/etc/passwd", "a/../../b.md", "C:/x.md", "a\\..\\b.md"):
        with pytest.raises(SourceError, match="outside"):
            extract("bad.zip", make_zip({bad: b"x"}))


def test_zip_member_limit(monkeypatch):
    monkeypatch.setattr(sources, "ZIP_MAX_MEMBERS", 3)
    data = make_zip({f"n{i}.md": b"x" for i in range(4)})
    with pytest.raises(SourceError, match="members"):
        extract("many.zip", data)


def test_zip_size_limit(monkeypatch):
    monkeypatch.setattr(sources, "ZIP_MAX_TOTAL", 1000)
    data = make_zip({"big.md": b"a" * 2000})
    with pytest.raises(SourceError, match="unpacks"):
        extract("big.zip", data)


def test_bad_zip_and_unknown():
    with pytest.raises(SourceError):
        extract("broken.zip", b"PK\x03\x04garbage")
    _, [item] = extract("photo.heic", b"\x00\x00binary")
    assert item.kind == "unknown" and item.reason == "unsupported format"


def test_audio_item():
    _, [item] = extract("voice-0412.m4a", b"\x00\x00\x00\x20ftypM4A data")
    assert item.kind == "audio" and item.audio_ext == "m4a"
    assert item.reason == "needs a transcript" and item.audio


def test_message_keys_and_slugify():
    a = sources.Message("2026-10-22T09:14:00", "Ana", "hi")
    b = sources.Message("2026-10-22T09:14:00", "Ana", "hi")
    assert a.key == b.key and a.key != sources.Message("2026-10-22T09:15:00", "Ana", "hi").key
    assert sources.slugify("Café à Noite!! / 2026") == "cafe-a-noite-2026"
    assert sources.slugify("***") == "item"
