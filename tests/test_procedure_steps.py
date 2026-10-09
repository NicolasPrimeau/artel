import importlib.util
import json
import pathlib
import subprocess
import uuid

import pytest

from tests.conftest import HEADERS

_HOOKS_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "_artel_hooks.py"


def _load_hooks():
    spec = importlib.util.spec_from_file_location("artel_hooks_steps", _HOOKS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hooks = _load_hooks()

ADD_SETTING = {
    "name": "add-setting",
    "nodes": [
        {
            "id": "field",
            "title": "Add the field to the Settings class",
            "done_check": {"kind": "git", "anchor": "config.py", "expect": "changed"},
        },
        {
            "id": "describe",
            "title": "Describe the setting in the config reference",
            "deps": ["field"],
            "done_check": {"kind": "git", "anchor": "reference.md", "expect": "changed"},
        },
    ],
}


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _commit(repo, **files):
    for name, body in files.items():
        (repo / name.replace("_", ".", 1)).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "work")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from artel.server.config import settings as server_settings

    _git(tmp_path, "init", "-q")
    _commit(tmp_path, config_py="interval = 60\n", reference_md="interval\n")
    monkeypatch.setattr(server_settings, "blueprint_repo_root", str(tmp_path))
    return tmp_path


async def _start(client, document=ADD_SETTING):
    await client.post("/blueprints", json={"document": document}, headers=HEADERS)
    r = await client.post(f"/blueprints/{document['name']}/instantiate", json={}, headers=HEADERS)
    assert r.status_code == 201
    return r.json()


async def _steps(client):
    r = await client.get("/blueprints/steps", headers=HEADERS)
    assert r.status_code == 200
    return r.json()


async def _run(client, run_id):
    return (await client.get(f"/blueprints/runs/{run_id}", headers=HEADERS)).json()


async def test_a_step_waits_until_the_repo_shows_the_work(client, repo):
    await _start(client)
    for _ in range(2):
        steps = await _steps(client)
        assert [s["node_id"] for s in steps] == ["field"]
    assert steps[0]["procedure"] == "add-setting"
    assert (steps[0]["position"], steps[0]["total"]) == (1, 2)
    assert steps[0]["done_when"] == "config.py changes in a commit"


async def test_a_commit_advances_the_procedure_with_no_task_call(client, repo):
    run = await _start(client)
    _commit(repo, config_py="interval = 60\nretries = 3\n")

    steps = await _steps(client)
    assert [s["node_id"] for s in steps] == ["describe"]
    assert steps[0]["position"] == 2

    _commit(repo, reference_md="interval\nretries\n")
    assert await _steps(client) == []
    assert (await _run(client, run["id"]))["status"] == "completed"


async def test_one_commit_can_finish_several_steps(client, repo):
    run = await _start(client)
    _commit(repo, config_py="interval = 60\nretries = 3\n", reference_md="interval\nretries\n")

    assert await _steps(client) == []
    assert (await _run(client, run["id"]))["status"] == "completed"


async def test_a_step_with_no_observable_check_is_left_for_the_agent(client, repo):
    document = {
        "name": "judged",
        "nodes": [
            {"id": "decide", "title": "Decide the default value"},
            {
                "id": "report",
                "title": "Report the value",
                "completion_contract": {"type": "object", "required": ["value"]},
                "done_check": {"kind": "git", "anchor": "config.py", "expect": "exists"},
            },
        ],
    }
    await _start(client, document)
    assert {s["node_id"] for s in await _steps(client)} == {"decide", "report"}


def _hook_steps(monkeypatch, steps, project):
    calls = []

    def fake_get(path, timeout=0):
        calls.append(path)
        return steps

    monkeypatch.setattr(hooks, "get", fake_get)
    monkeypatch.setattr(hooks, "AID", f"agent-{uuid.uuid4()}")
    return calls, {"session_id": f"s-{uuid.uuid4()}"}, project


_STEP = {
    "run_id": "r1",
    "procedure": "add-setting",
    "node_id": "describe",
    "task_id": "t2",
    "title": "Describe the setting in the config reference",
    "done_when": "reference.md changes in a commit",
    "position": 2,
    "total": 2,
}


def test_the_hook_nudges_one_line_once_per_step(monkeypatch):
    _, data, project = _hook_steps(monkeypatch, [_STEP], "artel")
    lines = hooks.step_lines(data, project)
    assert lines == [
        "Procedure add-setting, step 2/2: Describe the setting in the config reference. "
        "Done when reference.md changes in a commit"
    ]
    assert hooks.step_lines(data, project) == []
    assert hooks.step_lines(data, project) == []


def test_the_hook_nudges_again_when_the_step_changes(monkeypatch):
    _, data, project = _hook_steps(monkeypatch, [_STEP], "artel")
    assert hooks.step_lines(data, project)
    monkeypatch.setattr(hooks, "get", lambda path, timeout=0: [{**_STEP, "task_id": "t3"}])
    assert len(hooks.step_lines(data, project)) == 1


def test_a_long_step_still_fits_one_short_line(monkeypatch):
    novel = "Rework the entire ingestion path so that every capture is validated. " * 20
    step = {**_STEP, "title": novel, "done_when": novel}
    _, data, project = _hook_steps(monkeypatch, [step], "artel")
    (line,) = hooks.step_lines(data, project)
    assert len(line) <= 220
    assert "\n" not in line


def test_no_running_procedure_costs_one_request_then_nothing(monkeypatch):
    calls, data, project = _hook_steps(monkeypatch, [], f"p-{uuid.uuid4()}")
    assert hooks.step_lines(data, project) == []
    assert hooks.step_lines(data, project) == []
    assert len(calls) == 1


def test_recall_puts_the_step_before_the_memories(monkeypatch, capsys):
    _, data, _ = _hook_steps(monkeypatch, [_STEP], "artel")
    monkeypatch.setattr(hooks, "payload", lambda: {**data, "prompt": "how do we deploy the api"})
    monkeypatch.setattr(hooks, "resolve_project", lambda d=None: "artel")
    monkeypatch.setattr(
        hooks,
        "search",
        lambda q, limit=6, project="", max_distance=None: [
            {"id": "m1", "content": "deploy via fly.io", "type": "memory"}
        ],
    )
    hooks.cmd_recall()
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.splitlines()[0].startswith("[Artel] Procedure add-setting, step 2/2")
    assert "deploy via fly.io" in ctx
    assert len(ctx) <= hooks.INJECT_BUDGET
