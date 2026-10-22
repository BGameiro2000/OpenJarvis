"""The command line: scan, apply, state, decide (demo classifier, no model)."""

from __future__ import annotations

import json

from home_jarvis.intake.__main__ import main


def run(capsys, tmp_path, *args):
    code = main(["--vault", str(tmp_path), "--config", str(tmp_path / "intake.toml"), *args])
    return code, json.loads(capsys.readouterr().out)


def test_cli_scan_apply_state_decide(tmp_path, capsys):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "milk.md").write_text("Buy milk")
    (tmp_path / "inbox" / "musing.md").write_text("Some musing")
    code, plan = run(capsys, tmp_path, "--demo", "scan")
    assert code == 0 and plan["dry_run"] is True
    assert (tmp_path / "inbox" / "milk.md").exists()
    code, plan = run(capsys, tmp_path, "--demo", "scan", "--apply")
    assert plan["dry_run"] is False and not (tmp_path / "inbox" / "milk.md").exists()
    esc = next(i for i in plan["items"] if i["decision"] == "escalate")
    code, state = run(capsys, tmp_path, "state")
    assert [f["name"] for f in state["inbox"]] == ["musing.md"]
    code, out = run(
        capsys, tmp_path, "--demo", "decide", esc["id"], "edit", "--envelope", '{"route": "x"}'
    )
    assert code == 1 and out["ok"] is False and out["checks"]
    code, out = run(capsys, tmp_path, "--demo", "decide", esc["id"], "file")
    assert code == 0 and out["decision"] == "filed"
    code, tail = run(capsys, tmp_path, "log", "-n", "1")
    assert tail[0]["source"] == "musing.md" and tail[0]["by"] == "cli"


def test_cli_without_a_model_still_files_chats(tmp_path, capsys):
    from test_intake_sources import ANDROID

    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "WhatsApp Chat with Ana.txt").write_text(ANDROID)
    (tmp_path / "inbox" / "milk.md").write_text("Buy milk")
    code, plan = run(capsys, tmp_path, "--no-model", "scan")
    decisions = {i["source"]: i["decision"] for i in plan["items"]}
    assert decisions == {"WhatsApp Chat with Ana.txt": "file", "milk.md": "escalate"}
