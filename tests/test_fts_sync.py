from tests.conftest import HEADERS


def _patch_embeds(monkeypatch, fn):
    import artel.server.feed_poller as fp
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(fp, "embed", fn)
    monkeypatch.setattr(mem_routes, "embed", fn)


def _meta(gid, version=1, **kwargs):
    return {
        "memory_id": gid,
        "origin": "peer-instance",
        "version": version,
        "updated_at": f"2026-06-12T00:00:0{version}.000Z",
        "type": "memory",
        "agent_id": "mesh-peer",
        "confidence": 0.8,
        **kwargs,
    }


async def _search(client, q):
    r = await client.get("/memory/search", params={"q": q}, headers=HEADERS)
    assert r.status_code == 200
    return r.json()


async def test_feed_poller_write_is_keyword_searchable(client, monkeypatch):
    import artel.server.feed_poller as fp

    _patch_embeds(monkeypatch, lambda text: None)

    fp._write_memory("feed-agent", None, "quarterly observability digest", ["feed"])

    results = await _search(client, "observability digest")
    assert [e["content"] for e in results] == ["quarterly observability digest"]


async def test_replicated_entry_is_keyword_searchable(client, monkeypatch):
    import artel.server.feed_poller as fp
    import artel.store.db as db_mod

    _patch_embeds(monkeypatch, lambda text: None)

    feed = {"project": None, "agent_id": "mesh-peer"}
    assert fp._replicate_entry(
        db_mod.get_db(), feed, _meta("mesh-0001"), "replicated artifact ledger", [], "self-node"
    )

    results = await _search(client, "artifact ledger")
    assert [e["id"] for e in results] == ["mesh-0001"]


async def test_replicated_update_reindexes_keyword_search(client, monkeypatch):
    import artel.server.feed_poller as fp
    import artel.store.db as db_mod

    _patch_embeds(monkeypatch, lambda text: None)
    db = db_mod.get_db()
    feed = {"project": None, "agent_id": "mesh-peer"}

    assert fp._replicate_entry(db, feed, _meta("mesh-0002"), "draft cartography survey", [], "n1")
    assert fp._replicate_entry(
        db, feed, _meta("mesh-0002", version=2), "final topography survey", [], "n1"
    )

    assert [e["id"] for e in await _search(client, "topography")] == ["mesh-0002"]
    assert all(e["id"] != "mesh-0002" for e in await _search(client, "cartography"))


async def test_replicated_delete_drops_entry_from_search(client, monkeypatch):
    import artel.server.feed_poller as fp
    import artel.store.db as db_mod

    _patch_embeds(monkeypatch, lambda text: None)
    db = db_mod.get_db()
    feed = {"project": None, "agent_id": "mesh-peer"}

    assert fp._replicate_entry(db, feed, _meta("mesh-0003"), "perishable manifest", [], "n1")
    assert [e["id"] for e in await _search(client, "perishable")] == ["mesh-0003"]

    tombstone = _meta("mesh-0003", version=2, deleted_at="2026-06-12T01:00:00.000Z")
    assert fp._replicate_entry(db, feed, tombstone, "perishable manifest", [], "n1")
    assert await _search(client, "perishable") == []


async def test_replication_with_embeddings_down_still_indexes_keywords(client, monkeypatch):
    import artel.server.feed_poller as fp
    import artel.store.db as db_mod

    _patch_embeds(monkeypatch, lambda text: None)
    db = db_mod.get_db()
    feed = {"project": None, "agent_id": "mesh-peer"}

    assert fp._replicate_entry(db, feed, _meta("mesh-0004"), "degraded mode payload", [], "n1")

    vec_rows = db.execute("SELECT id FROM memory_vec WHERE id='mesh-0004'").fetchall()
    assert vec_rows == []
    assert [e["id"] for e in await _search(client, "degraded payload")] == ["mesh-0004"]
