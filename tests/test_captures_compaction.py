from unittest.mock import AsyncMock, MagicMock

import pytest

from artel.archivist import compaction
from artel.archivist.compaction import ExtractResult


def _client(pending):
    c = MagicMock()
    c.list_pending_captures = AsyncMock(return_value=pending)
    c.search_memory = AsyncMock(return_value=[])
    c.write_memory = AsyncMock()
    c.patch_memory = AsyncMock()
    c.digest_captures = AsyncMock()
    c.log = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_writes_fact_with_provenance_and_digests(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _client([{"id": "c1", "content": "we chose WAL", "session_id": "s1", "project": "p"}])
    extract = AsyncMock(return_value=ExtractResult(facts=["Artel uses WAL mode"], updates=[]))
    await compaction.run_capture_compaction(c, extract=extract)
    c.write_memory.assert_awaited_once()
    kwargs = c.write_memory.call_args.kwargs
    assert kwargs["project"] == "p"
    assert "session:s1" in kwargs["tags"]
    c.digest_captures.assert_awaited_once_with(["c1"])


@pytest.mark.asyncio
async def test_update_only_applied_against_returned_memory(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _client([{"id": "c1", "content": "x", "session_id": "s", "project": None}])
    c.search_memory = AsyncMock(return_value=[{"id": "m1", "content": "old"}])
    extract = AsyncMock(
        return_value=ExtractResult(
            facts=[], updates=[{"id": "m1", "content": "new"}, {"id": "ghost", "content": "x"}]
        )
    )
    await compaction.run_capture_compaction(c, extract=extract)
    c.patch_memory.assert_awaited_once_with("m1", content="new")  # ghost id ignored
    c.digest_captures.assert_awaited_once_with(["c1"])


@pytest.mark.asyncio
async def test_digests_even_when_nothing_extracted(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _client([{"id": "c1", "content": "chatter", "session_id": None, "project": None}])
    await compaction.run_capture_compaction(c, extract=AsyncMock(return_value=ExtractResult()))
    c.write_memory.assert_not_called()
    c.digest_captures.assert_awaited_once_with(["c1"])  # processed => drained


@pytest.mark.asyncio
async def test_passive_mode_leaves_captures_pending(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: False)
    c = _client([{"id": "c1", "content": "x", "session_id": None, "project": None}])
    extract = AsyncMock()
    await compaction.run_capture_compaction(c, extract=extract)
    extract.assert_not_called()
    c.write_memory.assert_not_called()
    c.digest_captures.assert_not_called()


@pytest.mark.asyncio
async def test_extract_error_leaves_capture_undigested(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _client([{"id": "c1", "content": "x", "session_id": None, "project": None}])
    await compaction.run_capture_compaction(c, extract=AsyncMock(side_effect=RuntimeError("down")))
    c.digest_captures.assert_not_called()  # retried next cycle
