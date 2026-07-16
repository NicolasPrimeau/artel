import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from artel.store import graph, hebbian
from artel.store.schema import SCHEMA

from .conftest import HEADERS


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _mem(db, *ids):
    for i in ids:
        db.execute(
            "INSERT INTO memory (id, type, agent_id, content) VALUES (?,?,?,?)",
            (i, "memory", "a", "x"),
        )


# --- pure store (no app) ---------------------------------------------------------------


def test_reinforce_creates_canonical_pairs(db):
    hebbian.reinforce(db, ["b", "a", "c"])
    rows = db.execute("SELECT src, dst, weight FROM hebbian_edge ORDER BY src, dst").fetchall()
    assert [(r["src"], r["dst"]) for r in rows] == [("a", "b"), ("a", "c"), ("b", "c")]
    assert all(r["weight"] == pytest.approx(hebbian.RATE) for r in rows)


def test_reinforce_is_order_insensitive_and_dedupes(db):
    hebbian.reinforce(db, ["a", "b"])
    hebbian.reinforce(db, ["b", "a", "a"])
    rows = db.execute("SELECT weight FROM hebbian_edge").fetchall()
    assert len(rows) == 1
    assert rows[0]["weight"] > hebbian.RATE  # second co-firing strengthened, not duplicated


def test_repeated_reinforcement_grows_but_stays_bounded(db):
    for _ in range(60):
        hebbian.reinforce(db, ["a", "b"])
    w = db.execute("SELECT weight FROM hebbian_edge").fetchone()["weight"]
    assert 0.9 < w <= 1.0


def test_neighbors_decay_over_time(db):
    hebbian.reinforce(db, ["a", "b"], now=_iso(hebbian.HALF_LIFE_DAYS))
    hebbian.reinforce(db, ["a", "c"], now=_iso(0))
    n = hebbian.neighbors(db, "a")
    assert n["b"] == pytest.approx(hebbian.RATE / 2, rel=1e-2)  # one half-life elapsed
    assert n["c"] == pytest.approx(hebbian.RATE, rel=1e-2)


def test_neighbors_filters_below_min_weight(db):
    hebbian.reinforce(db, ["a", "b"], now=_iso(hebbian.HALF_LIFE_DAYS * 10))
    assert hebbian.neighbors(db, "a") == {}  # evaporated below threshold


def test_neighbors_sees_both_directions(db):
    hebbian.reinforce(db, ["a", "b"])
    assert "b" in hebbian.neighbors(db, "a")
    assert "a" in hebbian.neighbors(db, "b")


def test_prune_removes_only_evaporated_edges(db):
    hebbian.reinforce(db, ["a", "b"], now=_iso(hebbian.HALF_LIFE_DAYS * 10))
    hebbian.reinforce(db, ["a", "c"], now=_iso(0))
    assert hebbian.prune(db) == 1
    remaining = db.execute("SELECT src, dst FROM hebbian_edge").fetchall()
    assert [(r["src"], r["dst"]) for r in remaining] == [("a", "c")]


# --- spreading activation integration --------------------------------------------------


def test_spread_activation_flows_over_hebbian_edges(db):
    _mem(db, "a", "b")
    hebbian.reinforce(db, ["a", "b"])
    hebbian.reinforce(db, ["a", "b"])
    db.commit()
    ranked = dict(graph.spread_activation(db, ["a"]))
    assert "b" in ranked  # no typed edges exist — pure behavioral association


def test_spread_activation_combines_semantic_and_hebbian(db):
    _mem(db, "a", "b", "c")
    graph.add_edge(db, None, "a", "b", "corroborates")
    for _ in range(3):
        hebbian.reinforce(db, ["a", "c"])
    db.commit()
    ranked = dict(graph.spread_activation(db, ["a"]))
    assert set(ranked) >= {"b", "c"}
    assert ranked["b"] > ranked["c"]  # semantic conductance outweighs behavioral


# --- co-retrieval via search (CI) -------------------------------------------------------


async def test_search_wires_co_retrieved_memories_together(client):
    import artel.store.db as db_mod

    await client.post("/memory", json={"content": "deploy pipeline uses fly.io"}, headers=HEADERS)
    await client.post("/memory", json={"content": "deploy pipeline needs secrets"}, headers=HEADERS)
    r = await client.get("/memory/search", params={"q": "deploy pipeline"}, headers=HEADERS)
    assert len(r.json()) == 2
    db = db_mod.get_db()
    row = db.execute("SELECT weight FROM hebbian_edge").fetchone()
    assert row is not None
    first = row["weight"]
    await client.get("/memory/search", params={"q": "deploy pipeline"}, headers=HEADERS)
    assert db.execute("SELECT weight FROM hebbian_edge").fetchone()["weight"] > first


async def test_search_hebbian_feeds_related_endpoint(client):
    import artel.store.db as db_mod

    r1 = await client.post("/memory", json={"content": "cache invalidation rules"}, headers=HEADERS)
    await client.post("/memory", json={"content": "cache warmup strategy"}, headers=HEADERS)
    for _ in range(3):
        await client.get("/memory/search", params={"q": "cache"}, headers=HEADERS)
    assert db_mod.get_db().execute("SELECT COUNT(*) FROM memory_edge").fetchone()[0] == 0
    r = await client.get(f"/memory/{r1.json()['id']}/related", headers=HEADERS)
    assert r.status_code == 200
    assert [e["content"] for e in r.json()] == ["cache warmup strategy"]


async def test_archivist_searches_do_not_reinforce(client):
    import artel.store.db as db_mod
    from artel.server.config import settings

    db = db_mod.get_db()
    db.execute(
        "INSERT INTO agents (id, api_key, role) VALUES (?, ?, 'owner')",
        (settings.archivist_agent_id, "archkey"),
    )
    db.commit()
    await client.post("/memory", json={"content": "quiet topic alpha"}, headers=HEADERS)
    await client.post("/memory", json={"content": "quiet topic beta"}, headers=HEADERS)
    arch_headers = {"x-agent-id": settings.archivist_agent_id, "x-api-key": "archkey"}
    await client.get("/memory/search", params={"q": "quiet topic"}, headers=arch_headers)
    assert db.execute("SELECT COUNT(*) FROM hebbian_edge").fetchone()[0] == 0
