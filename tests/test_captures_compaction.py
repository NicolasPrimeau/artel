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
    c._request = AsyncMock()
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


# --- major pass: refinement ---------------------------------------------------------


def _refine_client(delta):
    c = MagicMock()
    c.get_delta = AsyncMock(return_value=delta)
    c.patch_memory = AsyncMock()
    c.delete_memory = AsyncMock()
    c.log = AsyncMock()
    return c


def _prov(mid, content="fact", tags=("capture-extracted",)):
    return {"id": mid, "type": "memory", "content": content, "confidence": 0.6, "tags": list(tags)}


@pytest.mark.asyncio
async def test_refine_consolidates_and_corroborates(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _refine_client([_prov("m1"), _prov("m2")])
    refine = AsyncMock(
        return_value=[
            {
                "action": "consolidate",
                "keep": "m1",
                "drop": ["m2"],
                "content": "merged fact",
                "confidence": 0.95,
                "tags": ["capture-extracted", "infra"],
            }
        ]
    )
    await compaction.run_capture_refinement(c, refine=refine)
    # keep is patched with merged content + raised confidence + provisional tag dropped
    kwargs = c.patch_memory.call_args.kwargs
    assert kwargs["content"] == "merged fact"
    assert kwargs["confidence"] == 0.95
    assert "capture-extracted" not in kwargs["tags"] and "infra" in kwargs["tags"]
    # must NOT re-assert scope: provisional entries are scope=project with a null project,
    # and PATCH scope='project' without a project 422s. Tags-only PATCH preserves scope.
    assert "scope" not in kwargs
    c.delete_memory.assert_awaited_once_with("m2")


@pytest.mark.asyncio
async def test_refine_promote_drops_provisional_marker(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _refine_client([_prov("m1", tags=("capture-extracted", "keep")), _prov("m2")])
    refine = AsyncMock(return_value=[{"action": "promote", "id": "m1"}])
    await compaction.run_capture_refinement(c, refine=refine)
    c.patch_memory.assert_awaited_once_with("m1", tags=["keep"])


@pytest.mark.asyncio
async def test_refine_ignores_ops_outside_provisional_set(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _refine_client([_prov("m1"), _prov("m2")])
    # references a non-provisional id -> must be ignored (protects real memory)
    refine = AsyncMock(return_value=[{"action": "discard", "id": "other"}])
    await compaction.run_capture_refinement(c, refine=refine)
    c.delete_memory.assert_not_called()


@pytest.mark.asyncio
async def test_refine_skips_when_too_few_provisional(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _refine_client([_prov("m1")])  # only one
    refine = AsyncMock()
    await compaction.run_capture_refinement(c, refine=refine)
    refine.assert_not_called()


@pytest.mark.asyncio
async def test_each_capture_is_digested_as_it_lands(monkeypatch):
    """A pass killed mid-batch must keep the progress it made.

    Digesting only after the whole loop meant a 300s scheduler timeout discarded
    every capture's acknowledgement, so the queue re-served the same oldest rows
    forever and newer captures were never reached.
    """
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    caps = [
        {"id": f"c{i}", "content": f"fact {i}", "session_id": "s1", "project": "p"}
        for i in range(3)
    ]
    c = _client(caps)
    extract = AsyncMock(return_value=ExtractResult(facts=["a durable fact"], updates=[]))

    await compaction.run_capture_compaction(c, extract=extract)

    assert c.digest_captures.await_count == 3
    assert [call.args[0] for call in c.digest_captures.await_args_list] == [["c0"], ["c1"], ["c2"]]


@pytest.mark.asyncio
async def test_a_failed_capture_does_not_block_the_ones_behind_it(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    caps = [
        {"id": "bad", "content": "x", "session_id": "s1", "project": "p"},
        {"id": "good", "content": "y", "session_id": "s1", "project": "p"},
    ]
    c = _client(caps)
    extract = AsyncMock(
        side_effect=[RuntimeError("model blew up"), ExtractResult(facts=["kept"], updates=[])]
    )

    await compaction.run_capture_compaction(c, extract=extract)

    digested = [call.args[0][0] for call in c.digest_captures.await_args_list]
    assert digested == ["good"]


@pytest.mark.asyncio
async def test_pass_stops_at_its_budget_instead_of_being_cancelled(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    monkeypatch.setattr(compaction, "_PASS_BUDGET_SECONDS", 0.0)
    c = _client([{"id": "c0", "content": "x", "session_id": "s1", "project": "p"}])
    extract = AsyncMock(return_value=ExtractResult(facts=["f"], updates=[]))

    await compaction.run_capture_compaction(c, extract=extract)

    extract.assert_not_awaited()
    c.digest_captures.assert_not_awaited()
    c.log.assert_awaited()


@pytest.mark.asyncio
async def test_budget_keeps_the_work_done_before_it_expired(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    caps = [
        {"id": f"c{i}", "content": f"fact {i}", "session_id": "s1", "project": "p"}
        for i in range(4)
    ]
    c = _client(caps)
    # Pin the budget so the test does not depend on the production constant.
    monkeypatch.setattr(compaction, "_PASS_BUDGET_SECONDS", 240.0)

    # Replace the clock only inside compaction — patching the real time module
    # perturbs pytest's own timing. Each read advances 100s against a 240s budget,
    # so the pass gets through two captures and then stops.
    class _Clock:
        def __init__(self):
            self.t = 0.0

        def monotonic(self):
            self.t += 100.0
            return self.t

    monkeypatch.setattr(compaction, "time", _Clock())
    extract = AsyncMock(return_value=ExtractResult(facts=["f"], updates=[]))

    await compaction.run_capture_compaction(c, extract=extract)

    digested = [call.args[0][0] for call in c.digest_captures.await_args_list]
    assert digested == ["c0", "c1"], "work done before the budget expired must stay digested"
    c.log.assert_awaited()


@pytest.mark.asyncio
async def test_extracted_decisions_are_recorded_as_decisions(monkeypatch):
    """Decisions arrive from the pass, not from asking an agent to call decision_write.

    The instruction route was tried and measured: the preamble told agents to put
    decisions in memory_write and the decisions table held one row across the whole
    database. Hooks and passes run whether or not anyone remembered; instructions do
    not, so this is where the record has to come from.
    """
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _client(
        [{"id": "c1", "content": "postgres vs sqlite", "session_id": "s1", "project": "artel"}]
    )
    extract = AsyncMock(
        return_value=ExtractResult(
            facts=[],
            decisions=[
                {
                    "decision": "use SQLite",
                    "rationale": "single-file backup beats concurrency here",
                    "alternatives": ["Postgres", "DuckDB"],
                }
            ],
        )
    )
    await compaction.run_capture_compaction(c, extract=extract)

    c._request.assert_awaited_once()
    method, path = c._request.call_args.args[:2]
    body = c._request.call_args.kwargs["json"]
    assert (method, path) == ("POST", "/decisions")
    assert body["decision"] == "use SQLite"
    assert body["alternatives"] == ["Postgres", "DuckDB"]
    assert body["project"] == "artel"


@pytest.mark.asyncio
async def test_a_capture_with_no_decision_records_none(monkeypatch):
    monkeypatch.setattr(compaction, "is_configured", lambda: True)
    c = _client([{"id": "c1", "content": "renamed a variable", "session_id": "s1", "project": "p"}])
    await compaction.run_capture_compaction(
        c, extract=AsyncMock(return_value=ExtractResult(facts=["x"]))
    )
    c._request.assert_not_awaited()
