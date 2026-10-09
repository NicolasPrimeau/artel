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


def _run_recall(monkeypatch, capsys, search_results, session_id=None):
    session_id = session_id or f"s-{uuid.uuid4()}"
    monkeypatch.setattr(
        hooks, "payload", lambda: {"prompt": "how do we deploy the api", "session_id": session_id}
    )
    monkeypatch.setattr(hooks, "step_lines", lambda data, proj: [])
    monkeypatch.setattr(
        hooks, "search", lambda q, limit=6, project="", max_distance=None: search_results
    )
    hooks.cmd_recall()
    out = capsys.readouterr().out
    if not out.strip():
        return session_id, None
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    return session_id, ctx


def test_recall_injects_only_the_closest_note(monkeypatch, capsys):
    _, ctx = _run_recall(
        monkeypatch,
        capsys,
        [
            {"id": "m1", "content": "deploy via fly.io", "type": "memory"},
            {"id": "m2", "content": "staging needs secrets", "type": "memory"},
        ],
    )
    assert ctx == "[Artel] Note: deploy via fly.io"


def test_recall_adds_one_matching_skill(monkeypatch, capsys):
    _, ctx = _run_recall(
        monkeypatch,
        capsys,
        [
            {"id": "m1", "content": "deploy via fly.io", "type": "memory"},
            {"id": "k1", "content": "run the release checklist", "type": "skill"},
            {"id": "k2", "content": "rotate the deploy token", "type": "skill"},
        ],
    )
    assert ctx.splitlines() == [
        "[Artel] Note: deploy via fly.io",
        "Skill: run the release checklist",
    ]


def test_recall_is_silent_when_nothing_is_close(monkeypatch, capsys):
    _, ctx = _run_recall(monkeypatch, capsys, [])
    assert ctx is None


def test_recall_never_repeats_a_note_in_a_session(monkeypatch, capsys):
    sid = f"s-{uuid.uuid4()}"
    hit = [{"id": "m1", "content": "deploy via fly.io", "type": "memory"}]
    _, first = _run_recall(monkeypatch, capsys, hit, session_id=sid)
    _, second = _run_recall(monkeypatch, capsys, hit, session_id=sid)
    assert first is not None
    assert second is None


def test_search_lets_pruned_but_relevant_entries_surface(monkeypatch):
    from artel.archivist.config import settings as archivist_settings

    sent = {}

    def fake_get(path):
        sent["params"] = dict(hooks.urllib.parse.parse_qsl(hooks.urllib.parse.urlsplit(path).query))
        return []

    monkeypatch.setattr(hooks, "get", fake_get)
    hooks.search("how do we deploy the api")
    floor = float(sent["params"]["confidence_min"])
    assert floor > archivist_settings.decay_floor
    assert floor < 0.7**3


def _capture_params(monkeypatch):
    sent = []

    def fake_get(path):
        sent.append(dict(hooks.urllib.parse.parse_qsl(hooks.urllib.parse.urlsplit(path).query)))
        return []

    monkeypatch.setattr(hooks, "get", fake_get)
    return sent


def test_prompt_recall_drops_distant_matches(monkeypatch):
    sent = _capture_params(monkeypatch)
    monkeypatch.setattr(
        hooks, "payload", lambda: {"prompt": "how do we deploy the api", "session_id": "s-dist"}
    )
    hooks.cmd_recall()
    assert float(sent[0]["max_distance"]) == hooks.RECALL_MAX_DISTANCE


def test_file_gotcha_lookup_keeps_no_distance_cut(monkeypatch):
    sent = _capture_params(monkeypatch)
    hooks.search("auth.py auth", limit=4)
    assert "max_distance" not in sent[0]
