import sqlite3

import pytest

from artel.store import graph
from artel.store.schema import SCHEMA

from .conftest import HEADERS


def _seed(db):
    # m1 --corroborates--> m2 --relies_on--> m3 ;  m1 --contradicts--> m4 ;  m5 isolated
    for mid, content in [
        ("m1", "seed"),
        ("m2", "near"),
        ("m3", "far"),
        ("m4", "contra"),
        ("m5", "island"),
    ]:
        db.execute(
            "INSERT INTO memory (id, type, agent_id, content) VALUES (?,?,?,?)",
            (mid, "memory", "testagent", content),
        )
    graph.add_edge(db, None, "m1", "m2", "corroborates")
    graph.add_edge(db, None, "m2", "m3", "relies_on")
    graph.add_edge(db, None, "m1", "m4", "contradicts")
    db.commit()


@pytest.fixture
def bare_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# --- pure graph logic (no app / no embeddings) ---------------------------------------


def test_spread_activation_ranks_by_association(bare_db):
    _seed(bare_db)
    ids = [nid for nid, _ in graph.spread_activation(bare_db, ["m1"])]
    assert ids == ["m2", "m3"]  # corroborated neighbor, then its dependency (further = weaker)
    assert "m4" not in ids  # contradicted -> negative activation -> inhibited
    assert "m5" not in ids  # unconnected -> never activated


def test_spread_activation_decays_with_distance(bare_db):
    _seed(bare_db)
    scores = dict(graph.spread_activation(bare_db, ["m1"]))
    assert scores["m2"] > scores["m3"]  # one hop beats two hops


def test_spread_activation_no_seeds_or_edges(bare_db):
    assert graph.spread_activation(bare_db, []) == []
    bare_db.execute("INSERT INTO memory (id, type, agent_id, content) VALUES ('x','memory','a','y')")
    assert graph.spread_activation(bare_db, ["x"]) == []  # isolated seed -> nothing related


# --- endpoint (exercised by CI; the app fixture is heavy) ----------------------------


@pytest.mark.asyncio
async def test_related_endpoint_returns_associated_entries(client):
    import artel.store.db as db_mod

    _seed(db_mod.get_db())
    r = await client.get("/memory/m1/related", headers=HEADERS)
    assert r.status_code == 200
    assert [e["id"] for e in r.json()] == ["m2", "m3"]


@pytest.mark.asyncio
async def test_related_unknown_id_is_404(client):
    r = await client.get("/memory/nonexistent-xyzzy/related", headers=HEADERS)
    assert r.status_code == 404
