import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from artel.archivist import conflicts
from artel.store import graph
from artel.store.schema import SCHEMA


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    monkeypatch.setattr(conflicts, "get_db", lambda: conn)
    monkeypatch.setattr(conflicts, "is_configured", lambda: True)
    return conn


def _sibling(project="artel"):
    return {
        "id": "sib1",
        "content": "loser: cache TTL is 60s",
        "tags": ["sync-conflict"],
        "parents": ["win1"],
        "project": project,
    }


def _winner():
    return {"id": "win1", "content": "winner: cache TTL is 30s"}


def _client(siblings, winner=_winner()):
    c = MagicMock()
    c.list_entries = AsyncMock(return_value=siblings)
    c.get_memory = AsyncMock(return_value=winner)
    c.patch_memory = AsyncMock()
    c.delete_memory = AsyncMock()
    c.log = AsyncMock()
    return c


def _llm(monkeypatch, text):
    monkeypatch.setattr(conflicts, "complete", AsyncMock(return_value=text))


def _edge(db):
    graph.add_edge(db, "artel", "sib1", "win1", graph.CONTRADICTS)
    db.commit()


@pytest.mark.asyncio
async def test_merge_unifies_winner_and_drops_sibling(db, monkeypatch):
    _edge(db)
    client = _client([_sibling()])
    _llm(
        monkeypatch,
        '{"resolution": "merge", "content": "cache TTL is 30s (was 60s before the change)"}',
    )
    await conflicts.run_conflict_resolution(client)
    client.patch_memory.assert_awaited_once_with(
        "win1", content="cache TTL is 30s (was 60s before the change)"
    )
    client.delete_memory.assert_awaited_once_with("sib1")
    assert graph.edges_of(db, "sib1")["out"] == []  # contradicts edge cleaned up
    client.log.assert_awaited_once()


@pytest.mark.asyncio
async def test_keep_winner_discards_sibling_untouched_winner(db, monkeypatch):
    _edge(db)
    client = _client([_sibling()])
    _llm(monkeypatch, 'noise before {"resolution": "keep_winner"} noise after')
    await conflicts.run_conflict_resolution(client)
    client.patch_memory.assert_not_awaited()
    client.delete_memory.assert_awaited_once_with("sib1")
    assert graph.edges_of(db, "sib1")["out"] == []


@pytest.mark.asyncio
async def test_keep_both_retags_sibling_and_preserves_edge(db, monkeypatch):
    _edge(db)
    client = _client([_sibling()])
    _llm(monkeypatch, '{"resolution": "keep_both"}')
    await conflicts.run_conflict_resolution(client)
    client.delete_memory.assert_not_awaited()
    client.patch_memory.assert_awaited_once_with("sib1", tags=["conflict-kept"], scope="project")
    out = graph.edges_of(db, "sib1")["out"]
    assert any(e["rel"] == "contradicts" for e in out)  # genuine disagreement stays visible


@pytest.mark.asyncio
async def test_keep_both_without_project_skips_scope_pin(db, monkeypatch):
    client = _client([_sibling(project=None)])
    _llm(monkeypatch, '{"resolution": "keep_both"}')
    await conflicts.run_conflict_resolution(client)
    client.patch_memory.assert_awaited_once_with("sib1", tags=["conflict-kept"])


@pytest.mark.asyncio
async def test_orphaned_sibling_dropped_without_llm(db, monkeypatch):
    _edge(db)
    client = _client([_sibling()])
    client.get_memory = AsyncMock(side_effect=RuntimeError("404"))
    called = AsyncMock()
    monkeypatch.setattr(conflicts, "complete", called)
    await conflicts.run_conflict_resolution(client)
    called.assert_not_awaited()
    client.delete_memory.assert_awaited_once_with("sib1")


@pytest.mark.asyncio
async def test_invalid_llm_output_leaves_conflict_for_next_cycle(db, monkeypatch):
    client = _client([_sibling()])
    _llm(monkeypatch, "I think you should probably merge them somehow")
    await conflicts.run_conflict_resolution(client)
    client.patch_memory.assert_not_awaited()
    client.delete_memory.assert_not_awaited()
    client.log.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_without_content_is_rejected(db, monkeypatch):
    client = _client([_sibling()])
    _llm(monkeypatch, '{"resolution": "merge", "content": "  "}')
    await conflicts.run_conflict_resolution(client)
    client.patch_memory.assert_not_awaited()
    client.delete_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_llm_not_configured(db, monkeypatch):
    monkeypatch.setattr(conflicts, "is_configured", lambda: False)
    client = _client([_sibling()])
    await conflicts.run_conflict_resolution(client)
    client.list_entries.assert_not_awaited()


def test_parse_decision_contract():
    assert conflicts._parse_decision('{"resolution": "keep_winner"}') == {
        "resolution": "keep_winner"
    }
    assert conflicts._parse_decision("no json here") is None
    assert conflicts._parse_decision('{"resolution": "banana"}') is None
    assert conflicts._parse_decision('["resolution"]') is None
