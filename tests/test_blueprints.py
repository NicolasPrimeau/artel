import json

import pytest

from artel.store.db import get_db
from tests.conftest import HEADERS, HEADERS2

DISCOVER_CONTRACT = {
    "type": "object",
    "required": ["sources"],
    "properties": {
        "sources": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "object", "required": ["name"]},
        }
    },
}

MOLD = {
    "name": "new-product",
    "description": "discover sources, then probe each one",
    "params": ["domain"],
    "nodes": [
        {
            "id": "discover",
            "title": "Discover candidate sources for {domain}",
            "description": "Find every source that covers {domain}.",
            "tags": ["discovery"],
            "completion_contract": DISCOVER_CONTRACT,
            "done_check": {"kind": "payload", "path": "sources", "min_items": 1},
        },
        {
            "id": "probe",
            "title": "Probe {item.name}",
            "description": "Probe {item.name} for {domain}.",
            "deps": ["discover"],
            "foreach": "discover.sources",
        },
        {
            "id": "summarize",
            "title": "Summarize the {domain} probes",
            "deps": ["probe"],
        },
    ],
}


async def _put_blueprint(client, doc=None):
    r = await client.post("/blueprints", json={"document": doc or MOLD}, headers=HEADERS)
    assert r.status_code == 201, r.text
    return r.json()


async def _instantiate(client, params=None):
    r = await client.post(
        "/blueprints/new-product/instantiate",
        json={"params": params or {"domain": "liquor"}},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _complete(client, task_id, output=None):
    await client.post(f"/tasks/{task_id}/claim", headers=HEADERS2)
    r = await client.post(
        f"/tasks/{task_id}/complete",
        json={"body": "done", "output": output},
        headers=HEADERS2,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _nodes(run, node_id):
    return [n for n in run["nodes"] if n["node_id"] == node_id]


async def test_blueprint_is_versioned_on_recompile(client):
    first = await _put_blueprint(client)
    assert first["version"] == 1
    second = await _put_blueprint(client)
    assert second["version"] == 2
    listed = (await client.get("/blueprints", headers=HEADERS)).json()
    assert [b["id"] for b in listed] == [second["id"]]


async def test_blueprint_rejects_unknown_dependency(client):
    doc = {"name": "broken", "nodes": [{"id": "a", "title": "a", "deps": ["ghost"]}]}
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "unknown dependency" in json.dumps(r.json()["detail"])


async def test_blueprint_rejects_dependency_cycle(client):
    doc = {
        "name": "cyclic",
        "nodes": [
            {"id": "a", "title": "a", "deps": ["b"]},
            {"id": "b", "title": "b", "deps": ["a"]},
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "cycle" in json.dumps(r.json()["detail"])


async def test_blueprint_rejects_foreach_without_dependency(client):
    doc = {
        "name": "loose",
        "nodes": [
            {"id": "a", "title": "a"},
            {"id": "b", "title": "b", "foreach": "a.items"},
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "requires it as a dependency" in json.dumps(r.json()["detail"])


async def test_instantiate_requires_declared_params(client):
    await _put_blueprint(client)
    r = await client.post(
        "/blueprints/new-product/instantiate", json={"params": {}}, headers=HEADERS
    )
    assert r.status_code == 422
    assert "domain" in json.dumps(r.json()["detail"])


async def test_instantiate_materializes_only_the_root_wave(client):
    await _put_blueprint(client)
    run = await _instantiate(client)
    assert run["status"] == "running"
    assert len(run["nodes"]) == 1
    assert run["nodes"][0]["node_id"] == "discover"
    assert run["nodes"][0]["title"] == "Discover candidate sources for liquor"


async def test_root_task_carries_the_contract_and_backpointer(client):
    await _put_blueprint(client)
    run = await _instantiate(client)
    task = (await client.get(f"/tasks/{run['nodes'][0]['task_id']}", headers=HEADERS)).json()
    assert task["completion_contract"] == DISCOVER_CONTRACT
    assert f"run:{run['id']}:discover" in task["tags"]
    assert "discovery" in task["tags"]


async def test_completion_fans_out_one_task_per_item(client):
    await _put_blueprint(client)
    run = await _instantiate(client)
    await _complete(
        client,
        run["nodes"][0]["task_id"],
        {"sources": [{"name": "ontario"}, {"name": "quebec"}, {"name": "alberta"}]},
    )
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    probes = _nodes(after, "probe")
    assert len(probes) == 3
    assert sorted(p["title"] for p in probes) == [
        "Probe alberta",
        "Probe ontario",
        "Probe quebec",
    ]
    assert probes[0]["item"] == {"name": "ontario"}
    assert not _nodes(after, "summarize")


async def test_fanned_out_task_renders_run_params_too(client):
    await _put_blueprint(client)
    run = await _instantiate(client)
    await _complete(client, run["nodes"][0]["task_id"], {"sources": [{"name": "ontario"}]})
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    probe = (
        await client.get(f"/tasks/{_nodes(after, 'probe')[0]['task_id']}", headers=HEADERS)
    ).json()
    assert probe["description"] == "Probe ontario for liquor."


async def test_successor_waits_for_every_fanned_out_sibling(client):
    await _put_blueprint(client)
    run = await _instantiate(client)
    await _complete(client, run["nodes"][0]["task_id"], {"sources": [{"name": "a"}, {"name": "b"}]})
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    probes = _nodes(after, "probe")

    await _complete(client, probes[0]["task_id"])
    mid = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(mid, "summarize")
    assert mid["status"] == "running"

    await _complete(client, probes[1]["task_id"])
    end = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert len(_nodes(end, "summarize")) == 1


async def test_run_completes_when_the_last_task_lands(client):
    await _put_blueprint(client)
    run = await _instantiate(client)
    await _complete(client, run["nodes"][0]["task_id"], {"sources": [{"name": "only"}]})
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    await _complete(client, _nodes(after, "probe")[0]["task_id"])
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    await _complete(client, _nodes(after, "summarize")[0]["task_id"])
    final = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert final["status"] == "completed"
    row = (
        get_db()
        .execute(
            "SELECT payload FROM events WHERE type='blueprint.run.completed' ORDER BY created_at DESC"
        )
        .fetchone()
    )
    assert json.loads(row["payload"])["run_id"] == run["id"]


async def test_failed_done_check_spawns_remediation_instead_of_expanding(client):
    doc = json.loads(json.dumps(MOLD))
    doc["nodes"][0]["done_check"] = {"kind": "payload", "path": "sources", "min_items": 2}
    await _put_blueprint(client, doc)
    run = await _instantiate(client)
    await _complete(client, run["nodes"][0]["task_id"], {"sources": [{"name": "lonely"}]})

    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(after, "probe")
    discover = _nodes(after, "discover")
    assert len(discover) == 2
    remediation = (await client.get(f"/tasks/{discover[1]['task_id']}", headers=HEADERS)).json()
    assert remediation["title"].startswith("Remediate: ")
    assert "1 items, expected 2" in remediation["description"]
    assert "remediation" in remediation["tags"]


async def test_remediation_completion_resumes_the_run(client):
    doc = json.loads(json.dumps(MOLD))
    doc["nodes"][0]["done_check"] = {"kind": "payload", "path": "sources", "min_items": 2}
    await _put_blueprint(client, doc)
    run = await _instantiate(client)
    await _complete(client, run["nodes"][0]["task_id"], {"sources": [{"name": "lonely"}]})
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    remediation_id = _nodes(after, "discover")[1]["task_id"]

    await _complete(client, remediation_id, {"sources": [{"name": "a"}, {"name": "b"}]})
    resumed = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    probes = _nodes(resumed, "probe")
    assert sorted(p["title"] for p in probes) == ["Probe a", "Probe b"]
    assert [n["superseded"] for n in _nodes(resumed, "discover")] == [True, False]


async def test_sqlite_done_check_reads_reality(client):
    doc = {
        "name": "grounded",
        "nodes": [
            {
                "id": "seed",
                "title": "seed",
                "done_check": {
                    "kind": "sqlite",
                    "query": "SELECT 1 FROM tasks WHERE title='the receipt'",
                },
            },
            {"id": "next", "title": "next", "deps": ["seed"]},
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (await client.post("/blueprints/grounded/instantiate", json={}, headers=HEADERS)).json()

    await _complete(client, run["nodes"][0]["task_id"])
    blocked = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(blocked, "next")

    await client.post("/tasks", json={"title": "the receipt"}, headers=HEADERS)
    remediation = _nodes(blocked, "seed")[1]["task_id"]
    await _complete(client, remediation)
    passed = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert len(_nodes(passed, "next")) == 1


async def test_sqlite_done_check_refuses_non_select(client):
    doc = {
        "name": "hostile",
        "nodes": [
            {
                "id": "seed",
                "title": "seed",
                "done_check": {"kind": "sqlite", "query": "DELETE FROM tasks"},
            },
            {"id": "next", "title": "next", "deps": ["seed"]},
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (await client.post("/blueprints/hostile/instantiate", json={}, headers=HEADERS)).json()
    before = get_db().execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
    await _complete(client, run["nodes"][0]["task_id"])
    assert get_db().execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] >= before
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(after, "next")


async def test_ordinary_tasks_are_untouched_by_the_reactor(client):
    r = await client.post("/tasks", json={"title": "plain task"}, headers=HEADERS)
    task_id = r.json()["id"]
    completed = await _complete(client, task_id)
    assert completed["status"] == "completed"
    assert get_db().execute("SELECT COUNT(*) AS n FROM blueprint_runs").fetchone()["n"] == 0


async def test_source_stamp_round_trips_for_the_compiler(client):
    r = await client.post(
        "/blueprints",
        json={"document": MOLD, "source_entry_id": "skill-1", "source_version": 7},
        headers=HEADERS,
    )
    assert r.status_code == 201
    listed = (await client.get("/blueprints", headers=HEADERS)).json()
    assert listed[0]["source_entry_id"] == "skill-1"
    assert listed[0]["source_version"] == 7


async def test_compiled_blueprint_runs_end_to_end(client):
    from unittest.mock import AsyncMock, MagicMock

    from artel.archivist import blueprint_compile

    created: dict = {}

    async def create_blueprint(document, project=None, source_entry_id=None, source_version=None):
        r = await client.post(
            "/blueprints",
            json={
                "document": document,
                "source_entry_id": source_entry_id,
                "source_version": source_version,
            },
            headers=HEADERS,
        )
        assert r.status_code == 201, r.text
        created.update(r.json())
        return created

    archivist = MagicMock()
    archivist.list_entries = AsyncMock(
        return_value=[{"id": "skill-1", "version": 1, "content": "discover, then probe each"}]
    )
    archivist.list_blueprints = AsyncMock(return_value=[])
    archivist.create_blueprint = create_blueprint

    compiled = await blueprint_compile.run_blueprint_compilation(
        archivist, compiler=AsyncMock(return_value=MOLD)
    )
    assert compiled == 1

    run = await _instantiate(client)
    await _complete(
        client, run["nodes"][0]["task_id"], {"sources": [{"name": "on"}, {"name": "qc"}]}
    )
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert sorted(n["title"] for n in _nodes(after, "probe")) == ["Probe on", "Probe qc"]


async def test_payload_done_check_without_a_contract_is_rejected(client):
    doc = {
        "name": "uncheckable",
        "nodes": [
            {
                "id": "migrate",
                "title": "migrate",
                "done_check": {"kind": "payload", "path": "table_exists"},
            }
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "can never pass" in json.dumps(r.json()["detail"])


async def test_done_check_path_must_exist_in_the_contract(client):
    doc = {
        "name": "mismatched",
        "nodes": [
            {
                "id": "ingest",
                "title": "ingest",
                "completion_contract": {
                    "type": "object",
                    "properties": {"rows": {"type": "integer"}},
                },
                "done_check": {"kind": "payload", "path": "ghost"},
            }
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "not declared in the completion_contract" in json.dumps(r.json()["detail"])


async def test_min_items_done_check_on_a_scalar_is_rejected(client):
    doc = {
        "name": "scalar-minitems",
        "nodes": [
            {
                "id": "ingest",
                "title": "ingest",
                "completion_contract": {
                    "type": "object",
                    "properties": {"row_count": {"type": "integer"}},
                },
                "done_check": {"kind": "payload", "path": "row_count", "min_items": 1},
            }
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "requires 'row_count' to be an array" in json.dumps(r.json()["detail"])


async def test_a_root_level_array_output_is_accepted(client):
    doc = {
        "name": "array-root",
        "params": [],
        "nodes": [
            {
                "id": "enumerate",
                "title": "enumerate",
                "completion_contract": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object", "required": ["name"]},
                },
            },
            {
                "id": "handle",
                "title": "Handle {item.name}",
                "deps": ["enumerate"],
                "foreach": "enumerate",
            },
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (await client.post("/blueprints/array-root/instantiate", json={}, headers=HEADERS)).json()
    await _complete(client, run["nodes"][0]["task_id"], [{"name": "a"}, {"name": "b"}])
    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert sorted(n["title"] for n in _nodes(after, "handle")) == ["Handle a", "Handle b"]


# --- lowered nodes: bodies the server runs itself, no model, no agent -------------


async def test_a_machine_node_completes_without_an_agent(client):
    doc = {
        "name": "lowered",
        "nodes": [
            {
                "id": "gather",
                "title": "Count the tasks",
                "run": {"kind": "sqlite", "query": "SELECT COUNT(*) AS n FROM tasks"},
            }
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (await client.post("/blueprints/lowered/instantiate", json={}, headers=HEADERS)).json()

    node = _nodes(run, "gather")[0]
    assert node["status"] == "completed", "nobody claimed it; the server ran it"
    task = (await client.get(f"/tasks/{node['task_id']}", headers=HEADERS)).json()
    assert task["completion_payload"][0]["n"] >= 0


async def test_a_machine_node_feeds_a_prose_fan_out(client):
    """The point of keeping the bookkeeping identical: foreach needs no special case."""
    doc = {
        "name": "mixed",
        "nodes": [
            {
                "id": "discover",
                "title": "Find the work",
                "run": {
                    "kind": "constant",
                    "value": [{"name": "alpha"}, {"name": "beta"}, {"name": "gamma"}],
                },
            },
            {
                "id": "handle",
                "title": "Handle {item.name}",
                "deps": ["discover"],
                "foreach": "discover",
            },
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (await client.post("/blueprints/mixed/instantiate", json={}, headers=HEADERS)).json()

    assert _nodes(run, "discover")[0]["status"] == "completed"
    handled = sorted(n["title"] for n in _nodes(run, "handle"))
    assert handled == ["Handle alpha", "Handle beta", "Handle gamma"]
    assert all(n["status"] == "open" for n in _nodes(run, "handle")), "prose nodes still wait"


async def test_chained_machine_nodes_settle_in_one_pass(client):
    doc = {
        "name": "chain",
        "nodes": [
            {"id": "a", "title": "a", "run": {"kind": "constant", "value": {"step": 1}}},
            {
                "id": "b",
                "title": "b",
                "deps": ["a"],
                "run": {"kind": "constant", "value": {"step": 2}},
            },
            {
                "id": "c",
                "title": "c",
                "deps": ["b"],
                "run": {"kind": "constant", "value": {"step": 3}},
            },
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (await client.post("/blueprints/chain/instantiate", json={}, headers=HEADERS)).json()

    assert [n["node_id"] for n in run["nodes"]] == ["a", "b", "c"]
    assert all(n["status"] == "completed" for n in run["nodes"])
    assert run["status"] == "completed", "a fully lowered blueprint needs no agent at all"


async def test_machine_node_renders_run_params(client):
    doc = {
        "name": "rendered",
        "params": ["target"],
        "nodes": [
            {"id": "echo", "title": "echo", "run": {"kind": "constant", "value": "built {target}"}}
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (
        await client.post(
            "/blueprints/rendered/instantiate",
            json={"params": {"target": "liquor"}},
            headers=HEADERS,
        )
    ).json()
    task = (await client.get(f"/tasks/{_nodes(run, 'echo')[0]['task_id']}", headers=HEADERS)).json()
    assert task["completion_payload"] == "built liquor"


async def test_a_failing_action_fails_its_task_and_stops_the_branch(client):
    doc = {
        "name": "brokenaction",
        "nodes": [
            {"id": "bad", "title": "bad", "run": {"kind": "sqlite", "query": "SELECT * FROM nope"}},
            {"id": "after", "title": "after", "deps": ["bad"]},
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    run = (
        await client.post("/blueprints/brokenaction/instantiate", json={}, headers=HEADERS)
    ).json()
    assert _nodes(run, "bad")[0]["status"] == "failed"
    assert not _nodes(run, "after"), "successors must not expand from a failed action"


async def test_sqlite_action_refuses_anything_but_a_select(client):
    doc = {
        "name": "hostileaction",
        "nodes": [
            {"id": "x", "title": "x", "run": {"kind": "sqlite", "query": "DELETE FROM tasks"}}
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    before = (await client.get("/tasks", headers=HEADERS)).json()
    run = (
        await client.post("/blueprints/hostileaction/instantiate", json={}, headers=HEADERS)
    ).json()
    assert _nodes(run, "x")[0]["status"] == "failed"
    after = (await client.get("/tasks", headers=HEADERS)).json()
    assert len(after) >= len(before), "the delete must not have run"


async def test_unknown_action_kind_is_rejected_at_compile_time(client):
    doc = {
        "name": "unknownaction",
        "nodes": [{"id": "x", "title": "x", "run": {"kind": "shell", "value": "rm -rf /"}}],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 422
    assert "unknown action kind" in json.dumps(r.json()["detail"])


async def test_machine_node_output_is_still_contract_checked(client):
    doc = {
        "name": "contracted-machine",
        "nodes": [
            {
                "id": "x",
                "title": "x",
                "run": {"kind": "constant", "value": {"wrong": 1}},
                "completion_contract": {"type": "object", "required": ["sources"]},
            }
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 201, "a machine node may declare a contract like any other"


def test_lowered_fraction_reports_progress():
    from artel.server.blueprint import BlueprintDocument, lowered_fraction

    doc = BlueprintDocument(
        name="half",
        nodes=[
            {"id": "a", "title": "a", "run": {"kind": "constant", "value": 1}},
            {"id": "b", "title": "b"},
        ],
    )
    assert lowered_fraction(doc) == 0.5


async def test_blueprint_listing_reports_how_much_is_lowered(client):
    doc = {
        "name": "measured",
        "nodes": [
            {"id": "a", "title": "a", "run": {"kind": "constant", "value": 1}},
            {"id": "b", "title": "b", "deps": ["a"]},
            {"id": "c", "title": "c", "deps": ["b"]},
            {"id": "d", "title": "d", "deps": ["c"]},
        ],
    }
    await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    entry = [
        b
        for b in (await client.get("/blueprints", headers=HEADERS)).json()
        if b["name"] == "measured"
    ][0]
    assert entry["node_count"] == 4
    assert entry["lowered_nodes"] == 1
    assert entry["lowered_fraction"] == 0.25


# --- git-anchored checks: read what the agent DID, not what it said --------------


@pytest.fixture
def code_repo(tmp_path, monkeypatch):
    import subprocess

    from artel.server.config import settings as server_settings

    (tmp_path / "auth.py").write_text("def role_of(a):\n    return 'agent'\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(server_settings, "blueprint_repo_root", str(tmp_path))
    return tmp_path


def _edit_and_commit(repo, body):
    import subprocess

    (repo / "auth.py").write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "work"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


GIT_MOLD = {
    "name": "code-fix",
    "nodes": [
        {
            "id": "fix",
            "title": "Fix role_of",
            "completion_contract": {"type": "object", "required": ["done"]},
            "done_check": {"kind": "git", "anchor": "auth.py::role_of", "expect": "changed"},
        },
        {"id": "review", "title": "Review the fix", "deps": ["fix"]},
    ],
}


async def test_a_shaped_payload_cannot_satisfy_a_git_check(client, code_repo):
    """The whole point: an agent can claim success, but it cannot fake HEAD."""
    await client.post("/blueprints", json={"document": GIT_MOLD}, headers=HEADERS)
    run = (await client.post("/blueprints/code-fix/instantiate", json={}, headers=HEADERS)).json()

    # A perfectly valid payload — and no code was touched.
    await _complete(client, run["nodes"][0]["task_id"], {"done": True})

    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(after, "review"), "must not advance on an unverified claim"
    remediation = _nodes(after, "fix")[1]
    task = (await client.get(f"/tasks/{remediation['task_id']}", headers=HEADERS)).json()
    assert "unchanged since this task was created" in task["description"]


async def test_the_check_passes_once_the_code_actually_changes(client, code_repo):
    await client.post("/blueprints", json={"document": GIT_MOLD}, headers=HEADERS)
    run = (await client.post("/blueprints/code-fix/instantiate", json={}, headers=HEADERS)).json()

    _edit_and_commit(
        code_repo,
        "def role_of(a):\n    if a == 'archivist':\n        return a\n    return 'agent'\n",
    )
    await _complete(client, run["nodes"][0]["task_id"], {"done": True})

    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert len(_nodes(after, "review")) == 1, "real work advances the graph"


async def test_editing_the_wrong_function_does_not_satisfy_the_check(client, code_repo):
    """Symbol-level precision — a change somewhere else in the file is not the fix."""
    (code_repo / "auth.py").write_text(
        "def role_of(a):\n    return 'agent'\n\n\ndef unrelated():\n    return 1\n"
    )
    await client.post("/blueprints", json={"document": GIT_MOLD}, headers=HEADERS)
    run = (await client.post("/blueprints/code-fix/instantiate", json={}, headers=HEADERS)).json()
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=code_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "elsewhere"],
        cwd=code_repo,
        check=True,
        capture_output=True,
    )
    _edit_and_commit(
        code_repo, "def role_of(a):\n    return 'agent'\n\n\ndef unrelated():\n    return 999\n"
    )
    await _complete(client, run["nodes"][0]["task_id"], {"done": True})

    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(after, "review")


async def test_git_check_without_a_configured_repo_fails_loudly(client, monkeypatch):
    from artel.server.config import settings as server_settings

    monkeypatch.setattr(server_settings, "blueprint_repo_root", "")
    await client.post("/blueprints", json={"document": GIT_MOLD}, headers=HEADERS)
    run = (await client.post("/blueprints/code-fix/instantiate", json={}, headers=HEADERS)).json()
    await _complete(client, run["nodes"][0]["task_id"], {"done": True})

    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(after, "review"), "an unconfigured check must block, never pass by default"


async def test_an_unknown_done_check_kind_blocks_rather_than_passes(client):
    """A gate that does not recognise itself must refuse, never wave through.

    Found by sabotage testing: making evaluate() return True for an unknown kind
    left the whole suite green, so a typo'd check kind would have silently
    satisfied every gate in a blueprint.
    """
    doc = {
        "name": "typo-check",
        "nodes": [
            {
                "id": "gate",
                "title": "gate",
                "completion_contract": {"type": "object", "required": ["ok"]},
                "done_check": {"kind": "pyaload", "path": "ok"},
            },
            {"id": "after", "title": "after", "deps": ["gate"]},
        ],
    }
    r = await client.post("/blueprints", json={"document": doc}, headers=HEADERS)
    assert r.status_code == 201, "an unrecognised kind is a runtime refusal, not a parse error"

    run = (await client.post("/blueprints/typo-check/instantiate", json={}, headers=HEADERS)).json()
    await _complete(client, run["nodes"][0]["task_id"], {"ok": True})

    after = (await client.get(f"/blueprints/runs/{run['id']}", headers=HEADERS)).json()
    assert not _nodes(after, "after"), "an unknown check must not advance the graph"
    remediation = _nodes(after, "gate")[1]
    task = (await client.get(f"/tasks/{remediation['task_id']}", headers=HEADERS)).json()
    assert "unknown done-check kind" in task["description"]
