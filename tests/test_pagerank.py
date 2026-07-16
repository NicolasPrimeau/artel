import sqlite3

import pytest

from artel.store import graph
from artel.store.schema import SCHEMA

from .conftest import HEADERS


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _mem(db, *ids):
    for i in ids:
        db.execute(
            "INSERT INTO memory (id, type, agent_id, content) VALUES (?,?,?,?)",
            (i, "memory", "a", "x"),
        )


# --- pure PageRank (no app) ----------------------------------------------------------


def test_pagerank_ranks_supported_nodes_highest(db):
    # a,c --relies_on--> b ;  a --corroborates--> d
    _mem(db, "a", "b", "c", "d")
    graph.add_edge(db, None, "a", "b", "relies_on")
    graph.add_edge(db, None, "c", "b", "relies_on")
    graph.add_edge(db, None, "a", "d", "corroborates")
    db.commit()
    pr = graph.pagerank(db)
    assert pr["b"] > pr["d"] > pr["a"]  # relied-on-twice > corroborated-once > leaf
    assert abs(pr["a"] - pr["c"]) < 1e-6  # symmetric leaves score equally


def test_pagerank_empty_graph(db):
    assert graph.pagerank(db) == {}


# --- endpoint (CI) -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_central_endpoint(client):
    import artel.store.db as db_mod

    d = db_mod.get_db()
    _mem(d, "a", "b", "c")
    graph.add_edge(d, None, "a", "b", "relies_on")
    graph.add_edge(d, None, "c", "b", "relies_on")
    d.commit()
    r = await client.get("/memory/central", headers=HEADERS)
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()]
    assert ids and ids[0] == "b"  # most central first
