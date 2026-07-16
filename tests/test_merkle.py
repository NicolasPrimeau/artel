import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from artel.store import merkle
from artel.store.schema import SCHEMA

from .conftest import HEADERS
from .test_feeds import _artel_item, _peer_feed


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _mem(
    db, i, project="p", version=1, updated_at="U1", deleted_at=None, vclock=None, scope="project"
):
    db.execute(
        """INSERT INTO memory (id, type, agent_id, project, scope, content,
           version, updated_at, deleted_at, vclock) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (i, "memory", "a", project, scope, "x", version, updated_at, deleted_at, vclock),
    )


def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json = MagicMock(return_value=payload)
    m.raise_for_status = MagicMock()
    m.text = json.dumps(payload)
    m.headers = {"content-type": "application/json"}
    return m


# --- pure tree (no app) ----------------------------------------------------------------


def test_entry_hash_sensitive_to_every_field():
    base = merkle.entry_hash("i", 1, "u", None, None)
    assert merkle.entry_hash("j", 1, "u", None, None) != base
    assert merkle.entry_hash("i", 2, "u", None, None) != base
    assert merkle.entry_hash("i", 1, "v", None, None) != base
    assert merkle.entry_hash("i", 1, "u", "d", None) != base
    assert merkle.entry_hash("i", 1, "u", None, '{"a": 1}') != base
    assert merkle.entry_hash("i", 1, "u", None, None) == base


def test_empty_tree_has_stable_empty_root(db):
    t = merkle.tree(db, "p")
    assert t == {"root": merkle.EMPTY_ROOT, "buckets": {}}


def test_identical_sets_produce_identical_roots(db):
    other = sqlite3.connect(":memory:")
    other.row_factory = sqlite3.Row
    other.executescript(SCHEMA)
    for d in (db, other):
        _mem(d, "e1")
        _mem(d, "e2", version=3)
    assert merkle.tree(db, "p")["root"] == merkle.tree(other, "p")["root"]


def test_single_change_moves_root_and_only_its_bucket(db):
    _mem(db, "e1")
    _mem(db, "e2")
    before = merkle.tree(db, "p")
    db.execute("UPDATE memory SET version=2 WHERE id='e1'")
    after = merkle.tree(db, "p")
    assert after["root"] != before["root"]
    changed = merkle.bucket_of("e1")
    for b in after["buckets"]:
        if b == changed:
            assert after["buckets"][b] != before["buckets"].get(b)
        else:
            assert after["buckets"][b] == before["buckets"].get(b)


def test_tree_scopes_to_project_and_skips_agent_scope(db):
    _mem(db, "mine", project="p")
    _mem(db, "theirs", project="q")
    _mem(db, "private", project="p", scope="agent")
    entries = merkle.bucket_entries(db, "p", merkle.bucket_of("mine"))
    assert "mine" in entries
    assert merkle.bucket_entries(db, "p", merkle.bucket_of("theirs")) == {}
    assert merkle.bucket_entries(db, "p", merkle.bucket_of("private")) == {}


def test_tombstones_are_part_of_the_tree(db):
    _mem(db, "e1")
    before = merkle.tree(db, "p")["root"]
    db.execute("UPDATE memory SET deleted_at='D' WHERE id='e1'")
    assert merkle.tree(db, "p")["root"] != before


# --- endpoint (CI) ---------------------------------------------------------------------


async def test_merkle_endpoint_roundtrip(client):
    await client.post("/projects/artel/join", headers=HEADERS)
    r = await client.post(
        "/memory", json={"content": "merkle me", "project": "artel"}, headers=HEADERS
    )
    entry_id = r.json()["id"]
    r = await client.get("/memory/merkle?project=artel", headers=HEADERS)
    assert r.status_code == 200
    t = r.json()
    b = merkle.bucket_of(entry_id)
    assert t["root"] != merkle.EMPTY_ROOT
    assert b in t["buckets"]
    r = await client.get(f"/memory/merkle?project=artel&bucket={b}", headers=HEADERS)
    assert entry_id in r.json()["entries"]


async def test_merkle_endpoint_requires_auth(client):
    r = await client.get("/memory/merkle?project=artel")
    assert r.status_code in (401, 403)


async def test_feed_json_ids_filter(client):
    await client.post("/projects/artel/join", headers=HEADERS)
    r1 = await client.post(
        "/memory", json={"content": "wanted", "project": "artel"}, headers=HEADERS
    )
    await client.post("/memory", json={"content": "other", "project": "artel"}, headers=HEADERS)
    wanted = r1.json()["id"]
    r = await client.get(f"/memory/feed.json?ids={wanted}", headers=HEADERS)
    items = r.json()["items"]
    assert [i["_artel"]["memory_id"] for i in items] == [wanted]


# --- poller diff sync ------------------------------------------------------------------


async def test_merkle_equal_roots_skips_feed_fetch(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    feed = await _peer_feed(client)
    db = db_mod.get_db()
    local = merkle.tree(db, "artel")
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = _resp(200, {"root": local["root"], "buckets": local["buckets"]})
        await feed_poller._poll_feed(feed)
    assert mock_get.call_count == 1  # one probe, no bucket walk, no feed fetch
    assert db.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 0
    assert (
        db.execute("SELECT last_fetched_at FROM feed_subscriptions").fetchone()["last_fetched_at"]
        is not None
    )


async def test_merkle_fetches_only_differing_ids(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    feed = await _peer_feed(client)
    db = db_mod.get_db()
    gid = "merk-1"
    peer_hash = merkle.entry_hash(gid, 1, "2026-05-16T00:00:00.000Z", None, '{"A": 1}')
    b = merkle.bucket_of(gid)
    feed_payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "items": [_artel_item(gid, "peer-A", "merkle synced", vclock={"A": 1})],
    }
    urls = []

    def route(url, **kw):
        u = str(url)
        urls.append(u)
        if "/memory/merkle" in u and "bucket=" in u:
            return _resp(200, {"bucket": b, "entries": {gid: peer_hash}})
        if "/memory/merkle" in u:
            return _resp(200, {"root": "peer-root", "buckets": {b: "peer-bucket-hash"}})
        return _resp(200, feed_payload)

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.side_effect = route
        await feed_poller._poll_feed(feed)

    row = db.execute("SELECT content FROM memory WHERE id=?", (gid,)).fetchone()
    assert row["content"] == "merkle synced"
    fetches = [u for u in urls if "ids=" in u]
    assert len(fetches) == 1
    assert f"ids={gid}" in fetches[0]
    assert "include_deleted=true" in fetches[0]
    # replicated state hashes identically — the diff heals and stays healed
    assert merkle.bucket_entries(db, "artel", b) == {gid: peer_hash}


async def test_merkle_missing_falls_back_to_full_feed(client):
    import artel.store.db as db_mod
    from artel.server import feed_poller

    feed = await _peer_feed(client)
    feed_payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "items": [_artel_item("merk-2", "peer-A", "via legacy full feed")],
    }

    def route(url, **kw):
        if "/memory/merkle" in str(url):
            return _resp(404, {"detail": "not found"})
        m = _resp(200, feed_payload)
        m.headers = {"content-type": "application/feed+json"}
        return m

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.side_effect = route
        await feed_poller._poll_feed(feed)

    row = db_mod.get_db().execute("SELECT content FROM memory WHERE id='merk-2'").fetchone()
    assert row["content"] == "via legacy full feed"
