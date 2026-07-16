"""
Emergent-behavior scenarios: specialization, associative memory, knowledge
trails, and mesh self-assembly arising from local agent behavior — nothing
here is configured, assigned, or curated by anyone.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import artel.store.db as db_mod


def _ts(days_ago: float = 0.0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json = MagicMock(return_value=payload)
    m.raise_for_status = MagicMock()
    m.text = json.dumps(payload)
    m.headers = {"content-type": "application/json"}
    return m


# ── Ant-colony task routing ───────────────────────────────────────────────────


async def test_fleet_specialization_emerges_from_completed_work(scenario):
    """Two workers grind different kinds of tasks; the system learns who does what.

    Nobody declares 'dbworker owns database work' — the division of labor
    emerges purely from each worker's completion history.
    """
    dispatcher = await scenario.agent("dispatcher")
    dbworker = await scenario.agent("dbworker")
    uiworker = await scenario.agent("uiworker")

    for i in range(3):
        t = await dispatcher.create_task(f"migrate table batch {i}", tags=["database"])
        await dbworker.claim_task(t["id"])
        await dbworker.complete_task(t["id"])
        t = await dispatcher.create_task(f"polish widget {i}", tags=["frontend"])
        await uiworker.claim_task(t["id"])
        await uiworker.complete_task(t["id"])

    await dispatcher.create_task("add index to events table", tags=["database"])
    await dispatcher.create_task("fix dropdown styling", tags=["frontend"])

    db_first = (await dbworker.recommended_tasks())[0]["title"]
    ui_first = (await uiworker.recommended_tasks())[0]["title"]
    assert db_first == "add index to events table"
    assert ui_first == "fix dropdown styling"

    # a fresh agent with no history gets no specialized steer — priority order only
    rookie = await scenario.agent("rookie")
    rookie_titles = [t["title"] for t in await rookie.recommended_tasks()]
    assert set(rookie_titles) == {"add index to events table", "fix dropdown styling"}


async def test_specialization_fades_without_practice(scenario):
    """Affinity earned long ago evaporates; recent practice wins the recommendation."""
    dispatcher = await scenario.agent("dispatcher")
    worker = await scenario.agent("worker")

    for i in range(5):
        t = await dispatcher.create_task(f"old-specialty {i}", tags=["legacy"])
        await worker.claim_task(t["id"])
        await worker.complete_task(t["id"])
    db = db_mod.get_db()
    db.execute(
        "UPDATE task_affinity SET updated_at=? WHERE agent_id='worker' AND tag='legacy'",
        (_ts(365),),
    )
    db.commit()

    t = await dispatcher.create_task("recent-specialty", tags=["current"])
    await worker.claim_task(t["id"])
    await worker.complete_task(t["id"])

    await dispatcher.create_task("legacy chore", tags=["legacy"])
    await dispatcher.create_task("current chore", tags=["current"])
    titles = [t["title"] for t in await worker.recommended_tasks()]
    assert titles.index("current chore") < titles.index("legacy chore")


# ── Hebbian associative memory ────────────────────────────────────────────────


async def test_fleet_usage_wires_an_associative_memory(scenario):
    """One agent's repeated retrievals build associations a different agent can walk.

    No archivist runs, no semantic edge is ever asserted — the association is
    pure fleet behavior, then it powers /related for everyone.
    """
    oncall = await scenario.agent("oncall")
    newhire = await scenario.agent("newhire")

    incident = await oncall.write_memory("postgres failover drill: promote replica first")
    runbook = await oncall.write_memory("postgres failover checklist lives in ops/runbooks")

    for _ in range(3):
        await oncall.search_memory("postgres failover")

    db = db_mod.get_db()
    assert db.execute("SELECT COUNT(*) FROM memory_edge").fetchone()[0] == 0  # no semantic edges

    related = await newhire.related_memory(incident["id"])
    assert [e["id"] for e in related] == [runbook["id"]]


async def test_associations_decay_when_the_fleet_moves_on(scenario):
    """Co-retrieval from months ago no longer surfaces as related."""
    agent = await scenario.agent("historian")
    a = await agent.write_memory("quarterly report draft process")
    b = await agent.write_memory("quarterly numbers come from finance export")
    await agent.search_memory("quarterly report")

    db = db_mod.get_db()
    db.execute("UPDATE hebbian_edge SET updated_at=?", (_ts(365),))
    db.commit()

    assert await agent.related_memory(a["id"]) == []
    assert b["id"] is not None


# ── Stigmergic knowledge trails ───────────────────────────────────────────────


async def test_hot_knowledge_stays_findable_abandoned_knowledge_fades(scenario):
    """Two competing answers; the one the fleet keeps walking to wins ranking,
    even though the abandoned one was historically read far more."""
    veteran = await scenario.agent("veteran")
    team = [await scenario.agent(f"member-{i}") for i in range(3)]

    old = await veteran.write_memory("deploy procedure: use the jenkins job")
    new = await veteran.write_memory("deploy procedure: use the github action")

    db = db_mod.get_db()
    # the jenkins doc was heavily used once upon a time…
    db.execute(
        "UPDATE memory SET read_count=500, trail=10, trail_at=? WHERE id=?",
        (_ts(90), old["id"]),
    )
    db.commit()

    # …but today the fleet keeps reaching for the github action doc
    for member in team:
        await member.get_memory(new["id"])

    results = await team[0].search_memory("deploy procedure")
    assert results[0]["id"] == new["id"]


async def test_trail_is_earned_by_use_not_by_authorship(scenario):
    """A memory nobody retrieves gains no trail, whoever wrote it."""
    author = await scenario.agent("prolific-author")
    entry = await author.write_memory("unread magnum opus")
    db = db_mod.get_db()
    row = db.execute("SELECT trail FROM memory WHERE id=?", (entry["id"],)).fetchone()
    assert row["trail"] == 0.0


async def test_archivist_curation_leaves_no_footprints(scenario):
    """The archivist reads everything constantly — it must not fake a trail
    or wire associations, or curation would masquerade as fleet interest."""
    agent = await scenario.agent("writer")
    archivist = await scenario.archivist_agent()

    a = await agent.write_memory("secret sauce recipe alpha")
    await agent.write_memory("secret sauce recipe beta")

    await archivist.get_memory(a["id"])
    await archivist.search_memory("secret sauce")

    db = db_mod.get_db()
    assert db.execute("SELECT trail FROM memory WHERE id=?", (a["id"],)).fetchone()["trail"] == 0.0
    assert db.execute("SELECT COUNT(*) FROM hebbian_edge").fetchone()[0] == 0


# ── Gossip mesh self-assembly ─────────────────────────────────────────────────


async def test_mesh_triangle_closes_itself(scenario, monkeypatch):
    """A is linked to B; B is linked to C. Gossip makes A discover C through B,
    handshake with C (vouched by B), and link — no operator involved."""
    import artel.server.config as cfg_mod
    from artel.server import gossip

    monkeypatch.setattr(cfg_mod.settings, "public_url", "https://a.mesh.example")
    owner = await scenario.owner_agent()
    r = await owner._http.post(
        "/mesh/peers", json={"peer_url": "https://b.mesh.example", "peer_token": "tokB"}
    )
    assert r.status_code == 201

    def on_get(url, **kw):
        u = str(url)
        if "b.mesh.example/mesh/gossip" in u:
            return _resp(200, {"peers": [{"peer_url": "https://c.mesh.example", "project": None}]})
        return _resp(200, {"version": "https://jsonfeed.org/version/1.1", "items": []})

    def on_post(url, **kw):
        assert str(url) == "https://c.mesh.example/mesh/handshake"
        assert kw["json"]["via"] == "https://b.mesh.example"
        return _resp(200, {"token": "tokC"})

    with (
        patch("httpx.AsyncClient.get", side_effect=on_get),
        patch("httpx.AsyncClient.post", side_effect=on_post),
    ):
        assert await gossip.gossip_once() == 1
        assert await gossip.gossip_once() == 0  # converged: triangle already closed

    db = db_mod.get_db()
    peers = {r["peer_url"] for r in db.execute("SELECT peer_url FROM peer_links").fetchall()}
    assert peers == {"https://b.mesh.example", "https://c.mesh.example"}


async def test_gossiped_link_carries_memories_end_to_end(scenario, monkeypatch):
    """After gossip links C, a poll of C's feed replicates its memories here —
    discovery, trust, and sync compose into one emergent pipeline."""
    import artel.server.config as cfg_mod
    from artel.server import gossip
    from artel.server.feed_poller import _poll_feed

    monkeypatch.setattr(cfg_mod.settings, "public_url", "https://a.mesh.example")
    owner = await scenario.owner_agent()
    await owner._http.post(
        "/mesh/peers", json={"peer_url": "https://b.mesh.example", "peer_token": "tokB"}
    )

    c_item = {
        "id": "https://c.mesh.example/memory/gid-c1",
        "title": "born on C",
        "content_text": "born on C",
        "tags": [],
        "_artel": {
            "memory_id": "gid-c1",
            "type": "memory",
            "confidence": 1.0,
            "project": None,
            "scope": "project",
            "agent_id": "c-agent",
            "version": 1,
            "created_at": "2026-07-01T00:00:00.000Z",
            "updated_at": "2026-07-01T00:00:00.000Z",
            "deleted_at": None,
            "parents": [],
            "origin": "instance-c",
            "vclock": {"instance-c": 1},
        },
    }

    def on_get(url, **kw):
        u = str(url)
        if "b.mesh.example/mesh/gossip" in u:
            return _resp(200, {"peers": [{"peer_url": "https://c.mesh.example", "project": None}]})
        if "c.mesh.example/memory/merkle" in u:
            return _resp(404, {})
        if "c.mesh.example/memory/feed.json" in u:
            m = _resp(200, {"version": "https://jsonfeed.org/version/1.1", "items": [c_item]})
            m.headers = {"content-type": "application/feed+json"}
            return m
        return _resp(200, {"version": "https://jsonfeed.org/version/1.1", "items": []})

    def on_post(url, **kw):
        return _resp(200, {"token": "tokC"})

    with (
        patch("httpx.AsyncClient.get", side_effect=on_get),
        patch("httpx.AsyncClient.post", side_effect=on_post),
    ):
        assert await gossip.gossip_once() == 1
        db = db_mod.get_db()
        feed = db.execute(
            "SELECT f.* FROM feed_subscriptions f JOIN peer_links p ON p.feed_id=f.id"
            " WHERE p.peer_url='https://c.mesh.example'"
        ).fetchone()
        await _poll_feed(dict(feed))

    row = db_mod.get_db().execute("SELECT content, origin FROM memory WHERE id='gid-c1'").fetchone()
    assert row is not None
    assert row["content"] == "born on C"
    assert row["origin"] == "instance-c"
