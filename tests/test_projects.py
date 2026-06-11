from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


async def test_join_and_list_mine(client):
    r = await client.post("/projects/alpha/join", headers=HEADERS)
    assert r.status_code == 204

    r2 = await client.get("/projects/mine", headers=HEADERS)
    assert r2.status_code == 200
    project_ids = [p["project_id"] for p in r2.json()]
    assert "alpha" in project_ids


async def test_join_idempotent(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    r = await client.post("/projects/alpha/join", headers=HEADERS)
    assert r.status_code == 204


async def test_leave(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    r = await client.delete("/projects/alpha/leave", headers=HEADERS)
    assert r.status_code == 204

    r2 = await client.get("/projects/mine", headers=HEADERS)
    ids = [p["project_id"] for p in r2.json()]
    assert "alpha" not in ids


async def test_list_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    await client.post("/projects/alpha/join", headers=HEADERS2)

    r = await client.get("/projects/alpha/members", headers=HEADERS)
    assert r.status_code == 200
    agent_ids = [m["agent_id"] for m in r.json()]
    assert TEST_AGENT in agent_ids
    assert AGENT2 in agent_ids


async def test_list_members_requires_membership(client):
    await client.post("/projects/alpha/join", headers=HEADERS)

    r = await client.get("/projects/alpha/members", headers=HEADERS2)
    assert r.status_code == 403


async def test_project_memory_visible_to_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    await client.post("/projects/alpha/join", headers=HEADERS2)

    await client.post(
        "/memory",
        json={
            "content": "alpha secret",
            "type": "memory",
            "scope": "project",
            "project": "alpha",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=HEADERS2)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "alpha secret" in contents


async def test_project_memory_hidden_from_non_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)

    await client.post(
        "/memory",
        json={
            "content": "alpha secret",
            "type": "memory",
            "scope": "project",
            "project": "alpha",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=HEADERS2)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "alpha secret" not in contents


async def test_project_list_includes_members(client):
    await client.post("/projects/alpha/join", headers=HEADERS)

    r = await client.get("/projects", headers=HEADERS)
    assert r.status_code == 200
    projects = {p["name"]: p for p in r.json()}
    assert "alpha" in projects
    assert TEST_AGENT in projects["alpha"]["agents"]


async def test_create_project(client):
    r = await client.post("/projects", json={"name": "my-new-project"}, headers=HEADERS)
    assert r.status_code == 204

    r2 = await client.get("/projects/mine", headers=HEADERS)
    ids = [p["project_id"] for p in r2.json()]
    assert "my-new-project" in ids


async def test_create_project_idempotent(client):
    await client.post("/projects", json={"name": "dup-project"}, headers=HEADERS)
    r = await client.post("/projects", json={"name": "dup-project"}, headers=HEADERS)
    assert r.status_code == 204


async def test_project_name_case_insensitive_join(client):
    await client.post("/projects/Nimbus/join", headers=HEADERS)
    r = await client.get("/projects/mine", headers=HEADERS)
    ids = [p["project_id"] for p in r.json()]
    assert "nimbus" in ids
    assert "Nimbus" not in ids


async def test_project_case_variants_resolve_to_same_project(client):
    await client.post("/projects/Nimbus/join", headers=HEADERS)
    await client.post("/projects/NIMBUS/join", headers=HEADERS2)

    r = await client.get("/projects/nimbus/members", headers=HEADERS)
    assert r.status_code == 200
    agent_ids = sorted(m["agent_id"] for m in r.json())
    assert agent_ids == sorted([TEST_AGENT, AGENT2])


async def test_memory_project_case_normalized(client):
    await client.post("/projects/Nimbus/join", headers=HEADERS)
    await client.post("/projects/Nimbus/join", headers=HEADERS2)

    await client.post(
        "/memory",
        json={
            "content": "shared nimbus note",
            "type": "memory",
            "scope": "project",
            "project": "NIMBUS",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory?project=nimbus", headers=HEADERS2)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "shared nimbus note" in contents


async def test_task_project_case_normalized(client):
    await client.post("/projects/MyProj/join", headers=HEADERS)

    r = await client.post(
        "/tasks",
        json={"title": "T", "project": "MYPROJ"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["project"] == "myproj"

    r2 = await client.get("/tasks?project=myproj", headers=HEADERS)
    assert any(t["title"] == "T" for t in r2.json())


async def test_project_list_case_normalized(client):
    await client.post("/projects/Foo/join", headers=HEADERS)
    await client.post("/projects/FOO/join", headers=HEADERS2)

    r = await client.get("/projects", headers=HEADERS)
    names = [p["name"] for p in r.json()]
    assert "foo" in names
    assert "Foo" not in names
    assert "FOO" not in names


async def test_project_roles_and_clear(client):
    # first member of a project becomes its owner; later joiners are members
    await client.post("/projects/alpha/join", headers=HEADERS)
    r = await client.post(
        "/memory", json={"content": "map intel", "project": "alpha"}, headers=HEADERS
    )
    eid = r.json()["id"]
    await client.post("/projects/alpha/join", headers=HEADERS2)

    members = (await client.get("/projects/alpha/members", headers=HEADERS)).json()
    roles = {m["agent_id"]: m["role"] for m in members}
    assert roles[TEST_AGENT] == "owner"
    assert roles[AGENT2] == "member"

    # only an owner can clear
    assert (await client.post("/projects/alpha/clear", headers=HEADERS2)).status_code == 403
    assert (await client.post("/projects/alpha/clear", headers=HEADERS)).status_code == 204
    assert (await client.get(f"/memory/{eid}", headers=HEADERS)).status_code == 404

    # re-joining preserves the owner role (it is not reassigned by position)
    await client.post("/projects/alpha/join", headers=HEADERS)
    again = (await client.get("/projects/alpha/members", headers=HEADERS)).json()
    assert {m["agent_id"]: m["role"] for m in again}[TEST_AGENT] == "owner"


async def test_project_tasks_clear_is_owner_gated(client):
    await client.post("/projects/beta/join", headers=HEADERS)
    await client.post("/projects/beta/join", headers=HEADERS2)
    r = await client.post(
        "/tasks", json={"title": "round 1 relic", "project": "beta"}, headers=HEADERS
    )
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS2)
    await client.post(f"/tasks/{tid}/complete", headers=HEADERS2)
    await client.post("/tasks", json={"title": "stale claim", "project": "beta"}, headers=HEADERS2)

    # a plain member cannot clear; the project owner (or instance owner) can
    assert (await client.post("/projects/beta/tasks/clear", headers=HEADERS2)).status_code == 403
    assert (await client.post("/projects/beta/tasks/clear", headers=HEADERS)).status_code == 204
    rows = (await client.get("/tasks", params={"project": "beta"}, headers=HEADERS)).json()
    assert rows == []
