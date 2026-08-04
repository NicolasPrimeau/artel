import json

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
    doc["nodes"][0]["completion_contract"] = None
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
    doc["nodes"][0]["completion_contract"] = None
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

    async def create_blueprint(document, source_entry_id=None, source_version=None):
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
