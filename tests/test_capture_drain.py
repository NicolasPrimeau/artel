import importlib.util
import json
import pathlib

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "_artel_hooks.py"


def _load():
    spec = importlib.util.spec_from_file_location("artel_hooks", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hooks = _load()


def _line(role, content):
    return json.dumps({"type": role, "message": {"role": role, "content": content}})


def test_compress_keeps_reasoning_drops_tool_output():
    lines = [
        _line("user", "please fix the bug"),
        _line(
            "assistant",
            [
                {"type": "text", "text": "I will edit the file"},
                {"type": "tool_use", "name": "Edit", "input": {"huge": "x" * 5000}},
            ],
        ),
        _line("user", [{"type": "tool_result", "content": "y" * 5000}]),
    ]
    out = hooks.compress_transcript(lines)
    assert "please fix the bug" in out
    assert "I will edit the file" in out
    assert "[tool:Edit]" in out
    assert "x" * 100 not in out  # tool input not dumped
    assert "y" * 100 not in out  # tool result dropped


def test_compress_skips_unparseable_lines():
    assert hooks.compress_transcript(["not json", "", "{bad"]) == ""


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEL_SPOOL", str(tmp_path))
    return tmp_path


def _write_transcript(path, n, tag):
    path.write_text("\n".join(_line("user", f"{tag} message {i} " + "w" * 40) for i in range(n)))


def test_drain_ships_once_and_advances_cursor(spool, monkeypatch):
    posted = []
    monkeypatch.setattr(
        hooks, "_post_capture", lambda content, sid: posted.append((sid, content)) or True
    )

    transcript = spool / "t.jsonl"
    _write_transcript(transcript, 12, "alpha")  # well over the size floor
    (spool / "incoming.jsonl").write_text(
        json.dumps(
            {"session_id": "sess1", "transcript_path": str(transcript), "hook_event_name": "Stop"}
        )
        + "\n"
    )

    hooks.cmd_drain()
    assert len(posted) == 1
    assert posted[0][0] == "sess1"
    assert "alpha message" in posted[0][1]
    assert (spool / "cursor-sess1").exists()

    # second drain with no new transcript content ships nothing
    (spool / "incoming.jsonl").write_text(
        json.dumps(
            {"session_id": "sess1", "transcript_path": str(transcript), "hook_event_name": "Stop"}
        )
        + "\n"
    )
    hooks.cmd_drain()
    assert len(posted) == 1  # cursor prevented a re-ship


def test_drain_holds_back_trivial_until_precompact_forces(spool, monkeypatch):
    posted = []
    monkeypatch.setattr(
        hooks, "_post_capture", lambda content, sid: posted.append((sid, content)) or True
    )

    transcript = spool / "t.jsonl"
    transcript.write_text(_line("user", "hi"))  # below the floor
    marker = {"session_id": "s2", "transcript_path": str(transcript), "hook_event_name": "Stop"}
    (spool / "incoming.jsonl").write_text(json.dumps(marker) + "\n")
    hooks.cmd_drain()
    assert posted == []  # trivial slice held back

    # a PreCompact forces the flush even though the slice is still small
    (spool / "incoming.jsonl").write_text(
        json.dumps({**marker, "hook_event_name": "PreCompact"}) + "\n"
    )
    hooks.cmd_drain()
    assert len(posted) == 1
