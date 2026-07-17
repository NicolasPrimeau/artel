"""
Full conflict lifecycle: concurrent edits on two mesh instances produce a
preserved conflict sibling (vector clocks), which the archivist then resolves
semantically — replication and curation composing end to end.
"""

from unittest.mock import AsyncMock

import artel.store.db as db_mod
from artel.archivist import conflicts
from artel.server.feed_poller import conflict_sibling_id
from artel.store import graph
from tests.test_feeds import _artel_item, _poll_artel


class _HttpArchivistClient:
    def __init__(self, http):
        self._http = http

    async def list_entries(self, tag=None, limit=100, **kwargs):
        r = await self._http.get("/memory", params={"tag": tag, "limit": limit})
        r.raise_for_status()
        return r.json()

    async def get_memory(self, entry_id):
        r = await self._http.get(f"/memory/{entry_id}")
        r.raise_for_status()
        return r.json()

    async def patch_memory(self, entry_id, **fields):
        r = await self._http.patch(f"/memory/{entry_id}", json=fields)
        r.raise_for_status()
        return r.json()

    async def delete_memory(self, entry_id):
        r = await self._http.delete(f"/memory/{entry_id}")
        r.raise_for_status()

    async def log(self, **kwargs):
        pass


async def test_sync_conflict_lifecycle_resolved_by_archivist(scenario, monkeypatch):
    subscriber = await scenario.agent("mesh-subscriber")
    await subscriber._http.post("/projects/artel/join")
    r = await subscriber._http.post(
        "/feeds",
        json={
            "url": "http://peer/memory/feed.json?project=artel",
            "name": "Peer",
            "project": "artel",
        },
    )
    assert r.status_code == 201
    db = db_mod.get_db()
    feed = dict(db.execute("SELECT * FROM feed_subscriptions").fetchone())

    # concurrent edits on two instances: vclocks {A:1} vs {B:1}
    await _poll_artel(feed, [_artel_item("gid-x", "peer-A", "TTL is 60 seconds", vclock={"A": 1})])
    await _poll_artel(
        feed,
        [
            _artel_item(
                "gid-x",
                "peer-B",
                "TTL is 30 seconds",
                updated_at="2026-05-16T01:00:00.000Z",
                vclock={"B": 1},
            )
        ],
    )

    sib_id = conflict_sibling_id("gid-x", "TTL is 60 seconds")
    sib = db.execute("SELECT tags, parents FROM memory WHERE id=?", (sib_id,)).fetchone()
    assert sib is not None  # mesh preserved the loser

    archivist = await scenario.archivist_agent()
    monkeypatch.setattr(conflicts, "is_configured", lambda: True)
    monkeypatch.setattr(
        conflicts,
        "complete",
        AsyncMock(
            return_value='{"resolution": "merge", "content": "TTL is 30 seconds (reduced from 60)"}'
        ),
    )
    await conflicts.run_conflict_resolution(_HttpArchivistClient(archivist._http))

    winner = db.execute("SELECT content FROM memory WHERE id='gid-x'").fetchone()
    assert winner["content"] == "TTL is 30 seconds (reduced from 60)"
    gone = db.execute("SELECT deleted_at FROM memory WHERE id=?", (sib_id,)).fetchone()
    assert gone["deleted_at"] is not None  # sibling retired
    assert graph.edges_of(db, sib_id)["out"] == []  # contradicts edge cleaned up
    still_tagged = await archivist._http.get("/memory", params={"tag": "sync-conflict"})
    assert still_tagged.json() == []  # nothing left to resolve
