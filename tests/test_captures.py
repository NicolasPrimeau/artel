import pytest

import artel.store.db as db_mod

from .conftest import HEADERS, TEST_AGENT


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


@pytest.mark.asyncio
async def test_capture_queue_is_archivist_only(client):
    r = await client.post("/captures", json={"content": "slice for archivist"}, headers=HEADERS)
    cid = r.json()["id"]

    # a plain agent may append but cannot read or drain the queue
    assert (await client.get("/captures", headers=HEADERS)).status_code == 403
    assert (
        await client.post("/captures/digest", json={"ids": [cid]}, headers=HEADERS)
    ).status_code == 403

    # with the archivist role: can list pending and mark digested
    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='archivist' WHERE id=?", (TEST_AGENT,))
    db.commit()

    listed = await client.get("/captures", headers=HEADERS)
    assert listed.status_code == 200
    assert any(c["id"] == cid for c in listed.json())

    d = await client.post("/captures/digest", json={"ids": [cid]}, headers=HEADERS)
    assert d.status_code == 200
    assert d.json()["digested"] == 1

    # digested -> no longer pending
    again = await client.get("/captures", headers=HEADERS)
    assert all(c["id"] != cid for c in again.json())
