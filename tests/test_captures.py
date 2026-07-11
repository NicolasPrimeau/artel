import pytest

import artel.store.db as db_mod

from .conftest import HEADERS


@pytest.mark.asyncio
async def test_capture_append_returns_ack(client):
    r = await client.post(
        "/captures",
        json={"content": "raw session slice", "session_id": "s1", "project": "Proj"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"]
    assert body["expires_at"]


@pytest.mark.asyncio
async def test_capture_stored_but_not_searchable_or_fed(client):
    marker = "capturemarker9182zzqq"
    r = await client.post(
        "/captures", json={"content": marker, "session_id": "s1"}, headers=HEADERS
    )
    assert r.status_code == 201

    # it really was stored on the ingest queue (so the checks below are not vacuous)
    row = (
        db_mod.get_db()
        .execute("SELECT content, digested_at FROM captures WHERE content=?", (marker,))
        .fetchone()
    )
    assert row is not None
    assert row["digested_at"] is None  # pending

    # ...but it must NOT be reachable by memory_search (not embedded / not FTS-indexed)
    s = await client.get(f"/memory/search?q={marker}", headers=HEADERS)
    assert s.status_code == 200
    assert all(marker not in (e.get("content") or "") for e in s.json())

    # ...and it must NOT appear in the CRDT JSON feed (never propagated over the mesh)
    f = await client.get("/memory/feed.json", headers=HEADERS)
    assert marker not in f.text


@pytest.mark.asyncio
async def test_capture_ttl_clamped(client):
    r = await client.post("/captures", json={"content": "x", "ttl_hours": 100000}, headers=HEADERS)
    assert r.status_code == 201  # oversized TTL is clamped, not rejected
