from tests.conftest import HEADERS, HEADERS2

ARCHIVIST = "archivist-test"
ARCHIVIST_KEY = "archivist-test-key"
ARCH_HEADERS = {"x-agent-id": ARCHIVIST, "x-api-key": ARCHIVIST_KEY}


def _register_archivist(client_db):
    client_db.execute(
        "INSERT INTO agents (id, api_key, role) VALUES (?, ?, 'archivist')",
        (ARCHIVIST, ARCHIVIST_KEY),
    )
    client_db.commit()


async def test_archivist_cannot_write_to_phantom_project(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)

    r = await client.post(
        "/memory",
        json={
            "content": "brief for nothing",
            "type": "doc",
            "scope": "project",
            "project": "ghost",
            "tags": ["project-brief"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=ARCH_HEADERS,
    )
    assert r.status_code == 403
    assert (
        "phantom" in r.json()["detail"].lower() or "external presence" in r.json()["detail"].lower()
    )


async def test_archivist_can_write_to_project_with_members(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)
    await client.post("/projects/alpha/join", headers=HEADERS)

    r = await client.post(
        "/memory",
        json={
            "content": "brief for alpha",
            "type": "doc",
            "scope": "project",
            "project": "alpha",
            "tags": ["project-brief"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=ARCH_HEADERS,
    )
    assert r.status_code == 201


async def test_archivist_can_write_to_project_with_other_memory(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)
    await client.post("/projects/beta/join", headers=HEADERS)
    await client.post(
        "/memory",
        json={
            "content": "seed note from real agent",
            "type": "memory",
            "scope": "project",
            "project": "beta",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.delete("/projects/beta/leave", headers=HEADERS)

    r = await client.post(
        "/memory",
        json={
            "content": "synthesis for beta",
            "type": "doc",
            "scope": "project",
            "project": "beta",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=ARCH_HEADERS,
    )
    assert r.status_code == 201


async def test_archivist_can_write_to_project_with_tasks(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)
    await client.post("/projects/gamma/join", headers=HEADERS)
    await client.post("/tasks", json={"title": "real task", "project": "gamma"}, headers=HEADERS)
    await client.delete("/projects/gamma/leave", headers=HEADERS)

    r = await client.post(
        "/memory",
        json={
            "content": "brief for gamma",
            "type": "doc",
            "scope": "project",
            "project": "gamma",
            "tags": ["project-brief"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=ARCH_HEADERS,
    )
    assert r.status_code == 201


async def test_archivist_cannot_create_project(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)

    r = await client.post("/projects", json={"name": "phantom"}, headers=ARCH_HEADERS)
    assert r.status_code == 403


async def test_archivist_cannot_join_project(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)

    r = await client.post("/projects/some-project/join", headers=ARCH_HEADERS)
    assert r.status_code == 403


async def test_archivist_cannot_create_task_in_phantom_project(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)

    r = await client.post(
        "/tasks", json={"title": "ghost task", "project": "vapor"}, headers=ARCH_HEADERS
    )
    assert r.status_code == 403


async def test_non_archivist_can_still_create_new_projects(client):
    await client.post("/projects/brand-new/join", headers=HEADERS)
    r = await client.post(
        "/memory",
        json={
            "content": "first note for new project",
            "type": "memory",
            "scope": "project",
            "project": "brand-new",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201

    await client.post("/projects/another-new/join", headers=HEADERS2)
    r2 = await client.post(
        "/tasks", json={"title": "first task", "project": "another-new"}, headers=HEADERS2
    )
    assert r2.status_code == 201


async def test_archivist_can_write_agent_scoped_memory_with_no_project(client):
    import artel.store.db as db_mod

    _register_archivist(db_mod._conn)

    r = await client.post(
        "/memory",
        json={
            "content": "personal note",
            "type": "memory",
            "scope": "agent",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=ARCH_HEADERS,
    )
    assert r.status_code == 201
