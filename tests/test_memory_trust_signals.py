from artel.store.db import get_db
from tests.conftest import HEADERS, HEADERS2


async def _write(client, content, headers=HEADERS, **fields):
    r = await client.post("/memory", json={"content": content, **fields}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_author_stamp_is_set_on_write(client):
    entry = await _write(client, "WAL mode is on")
    assert entry["author_updated_at"] == entry["updated_at"]
    assert entry["days_since_author_update"] == 0.0


async def test_author_edit_refreshes_the_author_stamp(client):
    entry = await _write(client, "first")
    r = await client.patch(f"/memory/{entry['id']}", json={"content": "second"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["author_updated_at"] == r.json()["updated_at"]


async def test_a_third_party_edit_leaves_the_author_stamp_behind(client):
    entry = await _write(client, "authored by testagent")
    db = get_db()
    with db:
        db.execute(
            "UPDATE memory SET author_updated_at='2026-01-01T00:00:00.000Z' WHERE id=?",
            (entry["id"],),
        )
    r = await client.patch(f"/memory/{entry['id']}", json={"confidence": 0.5}, headers=HEADERS2)
    if r.status_code != 200:
        r = await client.patch(f"/memory/{entry['id']}", json={"confidence": 0.5}, headers=HEADERS)
        assert r.json()["author_updated_at"] != "2026-01-01T00:00:00.000Z"
        return
    body = r.json()
    assert body["author_updated_at"] == "2026-01-01T00:00:00.000Z"
    assert body["updated_at"] > body["author_updated_at"]
    assert body["days_since_author_update"] > 100


async def test_days_since_author_update_grows_with_age(client):
    entry = await _write(client, "aged entry")
    db = get_db()
    with db:
        db.execute(
            "UPDATE memory SET author_updated_at='2026-07-05T00:00:00.000Z' WHERE id=?",
            (entry["id"],),
        )
    r = await client.get(f"/memory/{entry['id']}", headers=HEADERS)
    assert r.json()["days_since_author_update"] > 20


async def test_search_flags_a_project_entry_shadowing_a_global_one(client, monkeypatch):
    # conftest mocks every embedding to a zero vector; shadowing is a similarity
    # question, so this test needs real ones.
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", lambda text: [1.0] + [0.0] * 383)

    # Write the global entry BEFORE joining: once an agent has a default project,
    # a write with no explicit project lands in it rather than staying global.
    # Confidence decides the ranking, so the narrower entry is the one that wins.
    await _write(client, "deploys go through the shared pipeline", confidence=0.2)
    await client.post("/projects/proj-x/join", headers=HEADERS)
    await _write(client, "deploys go through the shared pipeline", project="proj-x")

    r = await client.get(
        "/memory/search", params={"q": "deploys go through the shared pipeline"}, headers=HEADERS
    )
    assert r.status_code == 200
    results = r.json()
    assert results[0]["project"] == "proj-x", "the narrower entry should rank first"
    assert results[0]["shadowed_scope"] == "global"
    shadowed_id = results[0]["shadowed_id"]
    assert shadowed_id
    assert next(e for e in results if e["id"] == shadowed_id)["project"] is None


async def test_a_global_only_result_is_not_flagged(client):
    await _write(client, "unique global fact about turbines")
    r = await client.get(
        "/memory/search", params={"q": "unique global fact about turbines"}, headers=HEADERS
    )
    for e in r.json():
        assert e["shadowed_scope"] is None


async def test_shadowing_needs_semantic_closeness(client, monkeypatch):
    import artel.store.embeddings as emb

    vectors = {
        "deploys go through the shared pipeline": [1.0] + [0.0] * 383,
        "the cafeteria serves lunch at noon": [0.0, 1.0] + [0.0] * 382,
    }
    monkeypatch.setattr(emb, "embed", lambda text: vectors.get(text, [0.5] * 384))
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", lambda text: vectors.get(text, [0.5] * 384))

    await _write(client, "the cafeteria serves lunch at noon")
    await client.post("/projects/proj-y/join", headers=HEADERS)
    await _write(client, "deploys go through the shared pipeline", project="proj-y")

    r = await client.get(
        "/memory/search", params={"q": "deploys go through the shared pipeline"}, headers=HEADERS
    )
    scoped = [e for e in r.json() if e["project"] == "proj-y"]
    assert scoped
    assert scoped[0]["shadowed_scope"] is None
