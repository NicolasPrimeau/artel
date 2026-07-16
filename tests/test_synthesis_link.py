import sqlite3
from unittest.mock import MagicMock

import pytest

from artel.archivist import synthesis
from artel.store import graph
from artel.store.schema import SCHEMA


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    monkeypatch.setattr(synthesis, "get_db", lambda: conn)
    return conn


@pytest.mark.asyncio
async def test_link_op_emits_graph_edge(db):
    entries = [{"id": "a", "project": "p"}, {"id": "b", "project": "p"}]
    await synthesis._execute_operations(
        [{"op": "link", "src": "a", "dst": "b", "rel": "corroborates"}], MagicMock(), entries
    )
    out = graph.edges_of(db, "a")["out"]
    assert any(e["dst"] == "b" and e["rel"] == "corroborates" for e in out)


@pytest.mark.asyncio
async def test_link_op_rejects_hallucinated_self_and_bad_rel(db):
    entries = [{"id": "a", "project": None}, {"id": "b", "project": None}]
    await synthesis._execute_operations(
        [
            {"op": "link", "src": "a", "dst": "ghost", "rel": "corroborates"},  # hallucinated dst
            {"op": "link", "src": "a", "dst": "a", "rel": "corroborates"},  # self-link
            {"op": "link", "src": "a", "dst": "b", "rel": "grounds"},  # rel not allowed here
        ],
        MagicMock(),
        entries,
    )
    assert graph.edges_of(db, "a")["out"] == []  # all three rejected, graph stays empty
