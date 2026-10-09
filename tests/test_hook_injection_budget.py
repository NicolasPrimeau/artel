import importlib.util
import json
import pathlib
import uuid

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "_artel_hooks.py"


def _load():
    spec = importlib.util.spec_from_file_location("artel_hooks_budget", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hooks = _load()

_NOVEL = " ".join(
    f"Sentence {i} explains at length how the deployment pipeline behaves under load."
    for i in range(60)
)
_RUN_ON = "word " * 400


def _context(capsys):
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else None


def _entries(n, kind="memory", content=_NOVEL):
    return [{"id": f"{kind}{i}", "content": content, "type": kind} for i in range(n)]


def test_nudge_keeps_short_text_whole():
    assert hooks.nudge("deploy  via\nfly.io", 120) == "deploy via fly.io"


def test_nudge_stops_at_a_sentence_end():
    out = hooks.nudge(_NOVEL, 120)
    assert len(out) <= 120
    assert out.endswith(".")
    assert _NOVEL.startswith(out)


def test_nudge_never_cuts_inside_a_word():
    out = hooks.nudge(_RUN_ON, 120)
    assert len(out) <= 121
    assert out.endswith("word…")


def test_entry_line_prefers_the_headline():
    entry = {"content": _NOVEL, "headline": "Deploy pipeline stalls above 40 concurrent builds"}
    assert hooks.entry_line(entry, 120) == entry["headline"]


def test_fit_drops_whole_lines_over_budget():
    lines = ["a" * 40, "b" * 40, "c" * 40]
    assert hooks.fit(lines, 90) == lines[:2]


@pytest.mark.parametrize("content", [_NOVEL, _RUN_ON])
def test_recall_injection_stays_inside_the_budget(monkeypatch, capsys, content):
    monkeypatch.setattr(
        hooks,
        "payload",
        lambda: {"prompt": "how do we deploy the api", "session_id": f"s-{uuid.uuid4()}"},
    )
    found = _entries(4, content=content) + _entries(2, "skill", content)
    monkeypatch.setattr(hooks, "search", lambda q, limit=6, project="", max_distance=None: found)
    monkeypatch.setattr(hooks, "related", lambda eid, limit=2: _entries(2, "linked", content))
    hooks.cmd_recall()
    ctx = _context(capsys)
    assert len(ctx) <= hooks.INJECT_BUDGET
    assert len(ctx.splitlines()) <= 5


def test_gotcha_injection_stays_inside_the_budget(monkeypatch, capsys):
    monkeypatch.setattr(
        hooks,
        "payload",
        lambda: {
            "tool_input": {"file_path": "/repo/deploy.py"},
            "session_id": f"s-{uuid.uuid4()}",
        },
    )
    found = _entries(4, content="deploy.py " + _NOVEL)
    monkeypatch.setattr(hooks, "search", lambda q, limit=6, project="", max_distance=None: found)
    hooks.cmd_gotcha()
    ctx = _context(capsys)
    assert len(ctx) <= hooks.INJECT_BUDGET
    assert len(ctx.splitlines()) <= 3


def test_session_start_is_a_pointer_not_the_handoff(monkeypatch, capsys):
    handoff = {
        "created_at": "2026-08-11T16:21:00",
        "summary": _NOVEL,
        "next_steps": [_NOVEL] * 7,
        "in_progress": [_RUN_ON] * 4,
    }
    monkeypatch.setattr(
        hooks, "get", lambda path, timeout=0: {"last_handoff": handoff, "memory_delta": [{}] * 900}
    )
    hooks.cmd_session()
    ctx = _context(capsys)
    assert len(ctx) <= hooks.SESSION_BUDGET
    assert ctx.startswith("[Artel] Last session 2026-08-11: Sentence 0")
    assert "(+5 more)" in ctx
    assert "session_context()" in ctx
    assert "memory entries changed" not in ctx


def test_session_start_is_silent_without_a_handoff(monkeypatch, capsys):
    monkeypatch.setattr(hooks, "get", lambda path, timeout=0: {"last_handoff": None})
    hooks.cmd_session()
    assert _context(capsys) is None
