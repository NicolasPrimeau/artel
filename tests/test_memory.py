import pytest

from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


@pytest.fixture
def mem_payload():
    return {
        "content": "Paris is the capital of France",
        "type": "memory",
        "scope": "project",
        "tags": ["geo"],
        "parents": [],
        "confidence": 1.0,
    }


async def test_write_and_get(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    assert r.status_code == 201
    entry = r.json()
    assert entry["content"] == mem_payload["content"]
    assert entry["agent_id"] == TEST_AGENT

    r2 = await client.get(f"/memory/{entry['id']}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["id"] == entry["id"]


async def test_patch_content(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(
        f"/memory/{eid}", json={"content": "Berlin is the capital of Germany"}, headers=HEADERS
    )
    assert r2.status_code == 200
    assert r2.json()["content"] == "Berlin is the capital of Germany"
    assert r2.json()["version"] == 2


async def test_patch_confidence_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"confidence": 0.5}, headers=HEADERS2)
    assert r2.status_code == 403


async def test_patch_type_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"type": "doc"}, headers=HEADERS2)
    assert r2.status_code == 403


async def test_patch_content_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(f"/memory/{eid}", json={"content": "hijacked"}, headers=HEADERS2)
    assert r2.status_code == 403


async def test_delete(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.delete(f"/memory/{eid}", headers=HEADERS)
    assert r2.status_code == 204

    r3 = await client.get(f"/memory/{eid}", headers=HEADERS)
    assert r3.status_code == 404


async def test_delete_by_other_agent_forbidden(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.delete(f"/memory/{eid}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_search(client):
    await client.post(
        "/memory",
        json={
            "content": "alpha entry",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "beta entry",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory/search", params={"q": "alpha"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1


async def test_delta(client, mem_payload):
    await client.post("/memory", json=mem_payload, headers=HEADERS)

    r = await client.get(
        "/memory/delta", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert len(r.json()) >= 1


async def test_private_scope_hidden_from_others(client):
    r = await client.post(
        "/memory",
        json={
            "content": "secret",
            "type": "memory",
            "scope": "agent",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    eid = r.json()["id"]

    r2 = await client.get(f"/memory/{eid}", headers=HEADERS2)
    assert r2.status_code == 403


async def test_list_memory_by_type(client):
    await client.post(
        "/memory",
        json={
            "content": "doc entry",
            "type": "doc",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "memory entry",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", params={"type": "doc"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["type"] == "doc" for e in results)
    assert len(results) == 1


async def test_get_nonexistent(client):
    r = await client.get("/memory/does-not-exist", headers=HEADERS)
    assert r.status_code == 404


async def test_memory_event_written_to_db(client, mem_payload):
    await client.post("/memory", json=mem_payload, headers=HEADERS)

    r = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    assert r.status_code == 200
    events = r.json()
    types = [e["type"] for e in events]
    assert "memory.written" in types


async def test_list_filter_by_tag(client):
    await client.post(
        "/memory",
        json={
            "content": "tagged entry",
            "type": "memory",
            "scope": "project",
            "tags": ["deploy"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "other entry",
            "type": "memory",
            "scope": "project",
            "tags": ["infra"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", params={"tag": "deploy"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["tags"] == ["deploy"]


async def test_list_filter_by_agent(client):
    await client.post(
        "/memory",
        json={
            "content": "from agent1",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "from agent2",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS2,
    )

    r = await client.get("/memory", params={"agent": AGENT2}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["agent_id"] == AGENT2 for e in results)


async def test_list_filter_by_confidence_min(client):
    await client.post(
        "/memory",
        json={
            "content": "high confidence",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 0.9,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "low confidence",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 0.3,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", params={"confidence_min": 0.8}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all(e["confidence"] >= 0.8 for e in results)
    assert any(e["content"] == "high confidence" for e in results)
    assert not any(e["content"] == "low confidence" for e in results)


async def test_search_filter_by_tag(client):
    await client.post(
        "/memory",
        json={
            "content": "deploy pipeline config",
            "type": "memory",
            "scope": "project",
            "tags": ["deploy"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "deploy pipeline config",
            "type": "memory",
            "scope": "project",
            "tags": ["infra"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory/search", params={"q": "deploy", "tag": "deploy"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert all("deploy" in e["tags"] for e in results)


async def test_project_scope_no_project_visible_to_all(client, monkeypatch):
    import artel.server.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "agent_keys", "restricted-agent:restrictedkey:proj-a")

    import artel.store.db as db_mod

    db_mod.get_db().execute(
        "INSERT OR IGNORE INTO agents (id, api_key) VALUES (?, ?)",
        ("restricted-agent", "restrictedkey"),
    )
    db_mod.get_db().commit()

    restricted_headers = {"x-agent-id": "restricted-agent", "x-api-key": "restrictedkey"}

    await client.post(
        "/memory",
        json={
            "content": "shared with all",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    await client.post(
        "/memory",
        json={
            "content": "only proj-b members",
            "type": "memory",
            "scope": "project",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
            "project": "proj-b",
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=restricted_headers)
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert "shared with all" in contents
    assert "only proj-b members" not in contents


async def test_private_scope_hidden_from_list(client):
    await client.post(
        "/memory",
        json={
            "content": "my secret",
            "type": "memory",
            "scope": "agent",
            "tags": [],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )

    r = await client.get("/memory", headers=HEADERS2)
    assert r.status_code == 200
    assert not any(e["content"] == "my secret" for e in r.json())


async def test_soft_delete_not_in_list(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]
    await client.delete(f"/memory/{eid}", headers=HEADERS)

    r2 = await client.get("/memory", headers=HEADERS)
    assert not any(e["id"] == eid for e in r2.json())


async def test_get_memory_by_id_prefix(client):
    from tests.conftest import HEADERS

    r = await client.post(
        "/memory", json={"content": "prefix-resolvable memory entry"}, headers=HEADERS
    )
    eid = r.json()["id"]

    r2 = await client.get(f"/memory/{eid[:8]}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["id"] == eid


async def test_get_memory_unknown_prefix_404(client):
    from tests.conftest import HEADERS

    r = await client.get("/memory/nosuchid", headers=HEADERS)
    assert r.status_code == 404


async def test_bulk_delete(client, mem_payload):
    r1 = await client.post("/memory", json=mem_payload, headers=HEADERS)
    r2 = await client.post(
        "/memory", json={**mem_payload, "content": "second entry"}, headers=HEADERS
    )
    id1, id2 = r1.json()["id"], r2.json()["id"]

    r = await client.request("DELETE", "/memory", json={"ids": [id1, id2]}, headers=HEADERS)
    assert r.status_code == 204

    assert (await client.get(f"/memory/{id1}", headers=HEADERS)).status_code == 404
    assert (await client.get(f"/memory/{id2}", headers=HEADERS)).status_code == 404


async def test_bulk_delete_skips_unauthorized(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    await client.request("DELETE", "/memory", json={"ids": [eid]}, headers=HEADERS2)

    assert (await client.get(f"/memory/{eid}", headers=HEADERS)).status_code == 200


async def test_bulk_delete_ignores_unknown_ids(client):
    r = await client.request(
        "DELETE",
        "/memory",
        json={"ids": ["00000000-0000-0000-0000-000000000000"]},
        headers=HEADERS,
    )
    assert r.status_code == 204


async def test_distinct_reader_count_increments_once_per_agent(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]
    assert r.json()["distinct_reader_count"] == 0

    first = await client.get(f"/memory/{eid}", headers=HEADERS)
    assert first.json()["distinct_reader_count"] == 1
    again = await client.get(f"/memory/{eid}", headers=HEADERS)
    assert again.json()["distinct_reader_count"] == 1

    other = await client.get(f"/memory/{eid}", headers=HEADERS2)
    assert other.json()["distinct_reader_count"] == 2


async def test_min_distinct_readers_filter(client, mem_payload):
    r1 = await client.post("/memory", json=mem_payload, headers=HEADERS)
    popular = r1.json()["id"]
    r2 = await client.post("/memory", json={**mem_payload, "content": "lonely"}, headers=HEADERS)
    lonely = r2.json()["id"]

    await client.get(f"/memory/{popular}", headers=HEADERS)
    await client.get(f"/memory/{popular}", headers=HEADERS2)
    await client.get(f"/memory/{lonely}", headers=HEADERS)

    r = await client.get("/memory", params={"min_distinct_readers": 2}, headers=HEADERS)
    ids = [e["id"] for e in r.json()]
    assert popular in ids
    assert lonely not in ids


async def test_patch_if_match_matching_version_succeeds(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]
    assert r.json()["version"] == 1

    r2 = await client.patch(
        f"/memory/{eid}",
        json={"confidence": 0.5},
        headers={**HEADERS, "If-Match": "1"},
    )
    assert r2.status_code == 200
    assert r2.json()["version"] == 2


async def test_patch_if_match_stale_version_returns_409(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    # First write moves the entry to version 2
    await client.patch(f"/memory/{eid}", json={"confidence": 0.8}, headers=HEADERS)

    # A second caller still believes it is at version 1
    r2 = await client.patch(
        f"/memory/{eid}",
        json={"confidence": 0.3},
        headers={**HEADERS, "If-Match": "1"},
    )
    assert r2.status_code == 409

    # The conflicting write did not take effect
    cur = await client.get(f"/memory/{eid}", headers=HEADERS)
    assert cur.json()["confidence"] == 0.8


async def test_patch_without_if_match_is_last_write_wins(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]
    await client.patch(f"/memory/{eid}", json={"confidence": 0.8}, headers=HEADERS)

    # No If-Match header: write succeeds regardless of intervening version change
    r2 = await client.patch(f"/memory/{eid}", json={"confidence": 0.2}, headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["confidence"] == 0.2


async def test_patch_invalid_if_match_returns_400(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    eid = r.json()["id"]

    r2 = await client.patch(
        f"/memory/{eid}",
        json={"confidence": 0.5},
        headers={**HEADERS, "If-Match": "not-a-number"},
    )
    assert r2.status_code == 400


async def test_write_skill_entry_type(client):
    r = await client.post(
        "/memory",
        json={
            "content": "How to deploy: docker compose up -d",
            "type": "skill",
            "scope": "project",
            "tags": ["procedure"],
            "parents": [],
            "confidence": 1.0,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["type"] == "skill"

    eid = r.json()["id"]
    got = await client.get(f"/memory/{eid}", headers=HEADERS)
    assert got.json()["type"] == "skill"

    listed = await client.get("/memory", params={"type": "skill"}, headers=HEADERS)
    assert any(e["id"] == eid for e in listed.json())


async def test_write_defaults_to_joined_project(client, mem_payload):
    await client.post("/projects/delta/join", headers=HEADERS)
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["project"] == "delta"


async def test_write_no_project_stays_global_without_membership(client, mem_payload):
    r = await client.post("/memory", json=mem_payload, headers=HEADERS)
    assert r.status_code == 201
    assert r.json()["project"] is None


async def _make_archivist(client):
    from artel.store.db import get_db

    get_db().execute("UPDATE agents SET role='archivist' WHERE id=?", (AGENT2,))
    get_db().commit()


async def test_headline_requires_curator(client, mem_payload):
    entry = (await client.post("/memory", json=mem_payload, headers=HEADERS)).json()
    r = await client.patch(
        f"/memory/{entry['id']}/headline",
        json={"headline": "a summary", "headline_version": entry["version"]},
        headers=HEADERS,
    )
    assert r.status_code == 403


async def test_archivist_sets_headline_without_version_bump(client, mem_payload):
    await _make_archivist(client)
    entry = (await client.post("/memory", json=mem_payload, headers=HEADERS)).json()
    r = await client.patch(
        f"/memory/{entry['id']}/headline",
        json={"headline": "capital-of-France fact", "headline_version": entry["version"]},
        headers=HEADERS2,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["headline"] == "capital-of-France fact"
    assert body["version"] == entry["version"]
    got = (await client.get(f"/memory/{entry['id']}", headers=HEADERS)).json()
    assert got["headline"] == "capital-of-France fact"
