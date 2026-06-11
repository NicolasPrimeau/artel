"""RBAC scenarios: viewer / agent / archivist / owner role enforcement.

These lock in the security model:
- viewer  — read-only, no mutations
- agent   — normal read/write, no agent-admin
- archivist — cross-agent memory curation (incl. directives), no agent-admin
- owner   — full admin, including delete/rename/list any agent
- the registration key registers agents but no longer destroys them
"""


async def test_viewer_is_read_only(scenario):
    viewer = await scenario.viewer_agent()

    assert (await viewer._http.get("/memory")).status_code == 200
    assert (await viewer._http.get("/tasks")).status_code == 200
    assert (await viewer._http.get("/agents")).status_code == 200

    assert (await viewer._http.post("/memory", json={"content": "x"})).status_code == 403
    assert (await viewer._http.post("/tasks", json={"title": "x"})).status_code == 403
    assert (
        await viewer._http.post("/messages", json={"to": "broadcast", "body": "x"})
    ).status_code == 403
    assert (
        await viewer._http.post("/events", json={"type": "x", "payload": {}})
    ).status_code == 403


async def test_viewer_sees_everything_without_joining(scenario):
    """A viewer is a read-only observer of the WHOLE instance: project tasks,
    project messages, and even direct agent-to-agent messages are visible
    without joining any project — it just can't touch any of it."""
    a = await scenario.agent("talker")
    b = await scenario.agent("listener")
    assert (await a._http.post("/projects/warzone/join")).status_code in (200, 201, 204)
    assert (await b._http.post("/projects/warzone/join")).status_code in (200, 201, 204)
    task = await a._http.post("/tasks", json={"title": "hold the line", "project": "warzone"})
    assert task.status_code == 201
    assert (
        await a._http.post(
            "/messages", json={"to": "project:warzone", "subject": "s", "body": "rally at (4,4)"}
        )
    ).status_code == 201
    direct = await a._http.post(
        "/messages", json={"to": "listener", "subject": "s", "body": "enemy #5 at (8,4)"}
    )
    assert direct.status_code == 201

    viewer = await scenario.viewer_agent()
    tasks = await viewer._http.get("/tasks", params={"project": "warzone"})
    assert tasks.status_code == 200
    assert any(t["title"] == "hold the line" for t in tasks.json())
    msgs = await viewer._http.get("/messages")
    assert msgs.status_code == 200
    bodies = [m["body"] for m in msgs.json()]
    assert "rally at (4,4)" in bodies
    assert "enemy #5 at (8,4)" in bodies  # direct messages too — full observability
    one = await viewer._http.get(f"/messages/{direct.json()['id']}")
    assert one.status_code == 200
    # still strictly read-only
    assert (await viewer._http.post(f"/messages/{direct.json()['id']}/read")).status_code == 403


async def test_agent_cannot_perform_owner_admin(scenario):
    agent = await scenario.agent("plain")
    victim = await scenario.agent("victim")

    r = await agent._http.get("/agents")
    assert r.status_code == 200
    for a in r.json():
        assert a["api_key"] == ""

    assert (await agent._http.delete(f"/agents/{victim.id}")).status_code == 403
    assert (
        await agent._http.patch(f"/agents/{victim.id}", json={"new_id": "hijacked"})
    ).status_code == 403


async def test_owner_can_perform_admin(scenario):
    owner = await scenario.owner_agent()
    await scenario.agent("disposable")

    assert (await owner._http.get("/agents")).status_code == 200
    assert (await owner._http.delete("/agents/disposable")).status_code == 204


async def test_registration_key_no_longer_destroys_agents(scenario):
    """The core fix: the registration key registers agents but cannot delete or
    list them. Destruction is owner-only."""
    await scenario.agent("protected")

    assert (await scenario._admin.delete("/agents/protected")).status_code == 401
    assert (await scenario._admin.get("/agents")).status_code == 401

    # registration itself still works with the key (open registration preserved)
    r = await scenario._admin.post("/agents/register", json={"agent_id": "newcomer"})
    assert r.status_code == 201


async def test_archivist_curates_cross_agent_memory(scenario):
    author = await scenario.agent("author")
    archivist = await scenario.archivist_agent()
    bystander = await scenario.agent("bystander")

    entry = await author.write_memory("authored by author")
    eid = entry["id"]

    # a normal agent cannot mutate someone else's memory
    assert (
        await bystander._http.patch(f"/memory/{eid}", json={"content": "tampered"})
    ).status_code == 403
    assert (await bystander._http.delete(f"/memory/{eid}")).status_code == 403

    # the archivist can curate cross-agent memory
    assert (
        await archivist._http.patch(f"/memory/{eid}", json={"content": "curated"})
    ).status_code == 200
    assert (await archivist._http.delete(f"/memory/{eid}")).status_code == 204


async def test_archivist_is_not_agent_admin(scenario):
    """The archivist role is scoped to memory curation, not agent administration."""
    archivist = await scenario.archivist_agent()
    await scenario.agent("untouchable")

    r = await archivist._http.get("/agents")
    assert r.status_code == 200
    for a in r.json():
        assert a["api_key"] == ""
    assert (await archivist._http.delete("/agents/untouchable")).status_code == 403


async def test_directive_writes_require_curator(scenario):
    agent = await scenario.agent("scribe")
    archivist = await scenario.archivist_agent()
    owner = await scenario.owner_agent()

    assert (
        await agent._http.post("/memory", json={"content": "d", "type": "directive"})
    ).status_code == 403
    assert (
        await archivist._http.post("/memory", json={"content": "d", "type": "directive"})
    ).status_code == 201
    assert (
        await owner._http.post("/memory", json={"content": "d", "type": "directive"})
    ).status_code == 201
