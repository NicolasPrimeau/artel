import importlib.util
import json
import pathlib
import uuid

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "_artel_hooks.py"


def _load():
    spec = importlib.util.spec_from_file_location("artel_hooks_recall", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hooks = _load()


def _run_recall(monkeypatch, capsys, search_results, related_results, session_id=None):
    session_id = session_id or f"s-{uuid.uuid4()}"
    monkeypatch.setattr(
        hooks, "payload", lambda: {"prompt": "how do we deploy the api", "session_id": session_id}
    )
    monkeypatch.setattr(hooks, "search", lambda q, limit=6: search_results)
    monkeypatch.setattr(hooks, "related", lambda eid, limit=2: related_results)
    hooks.cmd_recall()
    out = capsys.readouterr().out
    if not out.strip():
        return session_id, None
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    return session_id, ctx


def test_recall_appends_graph_associates(monkeypatch, capsys):
    _, ctx = _run_recall(
        monkeypatch,
        capsys,
        [{"id": "m1", "content": "deploy via fly.io", "type": "memory"}],
        [{"id": "m9", "content": "fly token rotates monthly", "type": "memory"}],
    )
    assert "deploy via fly.io" in ctx
    assert "Linked in the knowledge graph" in ctx
    assert "fly token rotates monthly" in ctx


def test_recall_excludes_associates_already_surfaced_by_search(monkeypatch, capsys):
    _, ctx = _run_recall(
        monkeypatch,
        capsys,
        [
            {"id": "m1", "content": "deploy via fly.io", "type": "memory"},
            {"id": "m2", "content": "staging needs secrets", "type": "memory"},
        ],
        [{"id": "m2", "content": "staging needs secrets", "type": "memory"}],
    )
    assert "Linked in the knowledge graph" not in ctx


def test_recall_survives_empty_related(monkeypatch, capsys):
    _, ctx = _run_recall(
        monkeypatch,
        capsys,
        [{"id": "m1", "content": "deploy via fly.io", "type": "memory"}],
        [],
    )
    assert "deploy via fly.io" in ctx
    assert "Linked" not in ctx


def test_recall_dedupes_associates_within_session(monkeypatch, capsys):
    sid = f"s-{uuid.uuid4()}"
    _, first = _run_recall(
        monkeypatch,
        capsys,
        [{"id": "m1", "content": "deploy via fly.io", "type": "memory"}],
        [{"id": "m9", "content": "fly token rotates monthly", "type": "memory"}],
        session_id=sid,
    )
    assert "fly token rotates monthly" in first
    # same session, new search hit, same associate — must not re-inject
    _, second = _run_recall(
        monkeypatch,
        capsys,
        [{"id": "m2", "content": "api gateway config", "type": "memory"}],
        [{"id": "m9", "content": "fly token rotates monthly", "type": "memory"}],
        session_id=sid,
    )
    assert second is not None
    assert "fly token rotates monthly" not in second


def test_related_helper_swallows_failure(monkeypatch):
    monkeypatch.setattr(hooks, "get", lambda path: None)
    assert hooks.related("m1") == []
    monkeypatch.setattr(hooks, "get", lambda path: {"detail": "boom"})
    assert hooks.related("m1") == []
