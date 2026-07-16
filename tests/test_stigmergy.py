from datetime import UTC, datetime, timedelta

import pytest

from .conftest import HEADERS


def _patch_embed(monkeypatch, fn):
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", fn)


def _trail(entry_id):
    import artel.store.db as db_mod

    row = (
        db_mod.get_db()
        .execute("SELECT trail, trail_at FROM memory WHERE id=?", (entry_id,))
        .fetchone()
    )
    return row["trail"], row["trail_at"]


def _set_trail(entry_id, trail, days_ago=0.0):
    import artel.store.db as db_mod

    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    db = db_mod.get_db()
    with db:
        db.execute("UPDATE memory SET trail=?, trail_at=? WHERE id=?", (trail, stamp, entry_id))


async def test_search_hit_deposits_trail(client):
    r = await client.post("/memory", json={"content": "ant colony pheromone"}, headers=HEADERS)
    entry_id = r.json()["id"]
    await client.get("/memory/search", params={"q": "pheromone"}, headers=HEADERS)
    trail, trail_at = _trail(entry_id)
    assert trail == pytest.approx(1.0)
    assert trail_at is not None
    await client.get("/memory/search", params={"q": "pheromone"}, headers=HEADERS)
    trail, _ = _trail(entry_id)
    assert trail == pytest.approx(2.0, rel=1e-3)


async def test_get_deposits_trail(client):
    r = await client.post("/memory", json={"content": "direct read target"}, headers=HEADERS)
    entry_id = r.json()["id"]
    await client.get(f"/memory/{entry_id}", headers=HEADERS)
    trail, _ = _trail(entry_id)
    assert trail == pytest.approx(1.0)


async def test_deposit_evaporates_before_accumulating(client):
    r = await client.post("/memory", json={"content": "old beaten path"}, headers=HEADERS)
    entry_id = r.json()["id"]
    _set_trail(entry_id, 8.0, days_ago=7.0)  # one trail half-life ago
    await client.get(f"/memory/{entry_id}", headers=HEADERS)
    trail, _ = _trail(entry_id)
    assert trail == pytest.approx(8.0 / 2 + 1.0, rel=1e-2)  # evaporated, then one deposit


async def test_hot_trail_ranks_above_cold(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)
    await client.post("/memory", json={"content": "water source at ridge"}, headers=HEADERS)
    hot = await client.post("/memory", json={"content": "water source at river"}, headers=HEADERS)
    _set_trail(hot.json()["id"], 8.0)
    r = await client.get("/memory/search", params={"q": "water source"}, headers=HEADERS)
    assert r.json()[0]["content"] == "water source at river"


async def test_evaporated_trail_no_longer_boosts(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)
    stale = await client.post("/memory", json={"content": "food cache in oak"}, headers=HEADERS)
    fresh = await client.post("/memory", json={"content": "food cache in pine"}, headers=HEADERS)
    _set_trail(stale.json()["id"], 8.0, days_ago=70.0)  # ten half-lives: effectively gone
    _set_trail(fresh.json()["id"], 2.0, days_ago=0.0)
    r = await client.get("/memory/search", params={"q": "food cache"}, headers=HEADERS)
    assert r.json()[0]["content"] == "food cache in pine"


async def test_archivist_reads_leave_no_trail(client):
    import artel.store.db as db_mod
    from artel.server.config import settings

    db = db_mod.get_db()
    db.execute(
        "INSERT INTO agents (id, api_key) VALUES (?, ?)",
        (settings.archivist_agent_id, "archkey"),
    )
    db.commit()
    r = await client.post("/memory", json={"content": "untouched by curation"}, headers=HEADERS)
    entry_id = r.json()["id"]
    arch = {"x-agent-id": settings.archivist_agent_id, "x-api-key": "archkey"}
    await client.get(f"/memory/{entry_id}", headers=arch)
    await client.get("/memory/search", params={"q": "untouched curation"}, headers=arch)
    trail, _ = _trail(entry_id)
    assert trail == 0.0


async def test_migration_backfills_trail_from_read_history(tmp_path):
    import sqlite3

    from artel.store.db import _migrate
    from artel.store.schema import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # simulate a pre-trail database: drop the columns, then re-migrate
    conn.execute("ALTER TABLE memory DROP COLUMN trail")
    conn.execute("ALTER TABLE memory DROP COLUMN trail_at")
    conn.execute(
        "INSERT INTO memory (id, type, agent_id, content, read_count, last_read_at)"
        " VALUES ('m1','memory','a','x', 25, '2026-07-01T00:00:00.000Z')"
    )
    conn.execute(
        "INSERT INTO memory (id, type, agent_id, content, read_count) VALUES ('m2','memory','a','y', 0)"
    )
    _migrate(conn)
    r1 = conn.execute("SELECT trail, trail_at FROM memory WHERE id='m1'").fetchone()
    r2 = conn.execute("SELECT trail, trail_at FROM memory WHERE id='m2'").fetchone()
    assert r1["trail"] == 10  # capped seed from read history
    assert r1["trail_at"] == "2026-07-01T00:00:00.000Z"
    assert r2["trail"] == 0
    assert r2["trail_at"] is None
