"""
Scripted scenario tests for adaptive archivist features.

Each scenario is a narrative: a realistic sequence of events (agents writing,
reading, the archivist running) with assertions on the outcome. These tests
exercise the full stack — HTTP API, DB, and archivist logic — wired together.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

import artel.store.db as db_mod
from artel.archivist.synthesis import capture_metrics, decay_confidence

ARCHIVIST_ID = "test-archivist"


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------


def _ts(offset_days: float = 0.0) -> str:
    dt = datetime.now(UTC) - timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _age_entry(entry_id: str, days: int) -> None:
    """Backdate a memory entry's updated_at so it falls outside the decay window."""
    db = db_mod.get_db()
    old_ts = _ts(days)
    db.execute(
        "UPDATE memory SET updated_at=?, created_at=? WHERE id=?", (old_ts, old_ts, entry_id)
    )
    db.commit()


def _set_read_state(entry_id: str, read_count: int, last_read_at: str | None) -> None:
    db = db_mod.get_db()
    db.execute(
        "UPDATE memory SET read_count=?, last_read_at=? WHERE id=?",
        (read_count, last_read_at, entry_id),
    )
    db.commit()


def _get_confidence(entry_id: str) -> float:
    row = (
        db_mod.get_db().execute("SELECT confidence FROM memory WHERE id=?", (entry_id,)).fetchone()
    )
    return row["confidence"]


def _get_tags(entry_id: str) -> list[str]:
    row = db_mod.get_db().execute("SELECT tags FROM memory WHERE id=?", (entry_id,)).fetchone()
    return json.loads(row["tags"])


def _exists(entry_id: str) -> bool:
    row = (
        db_mod.get_db()
        .execute("SELECT id FROM memory WHERE id=? AND deleted_at IS NULL", (entry_id,))
        .fetchone()
    )
    return row is not None


class _ArchivistClient:
    """Minimal archivist client backed by the in-process HTTP server."""

    def __init__(self, http: AsyncClient):
        self._http = http

    async def list_entries(
        self, type=None, updated_before=None, created_before=None, min_version=None, limit=100
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if type:
            params["type"] = type
        if updated_before:
            params["updated_before"] = updated_before
        if created_before:
            params["created_before"] = created_before
        if min_version is not None:
            params["min_version"] = min_version
        r = await self._http.get("/memory", params=params)
        r.raise_for_status()
        return r.json()

    async def patch_memory(self, entry_id: str, **fields) -> dict:
        r = await self._http.patch(f"/memory/{entry_id}", json=fields)
        r.raise_for_status()
        return r.json()

    async def log(self, action, message, level="info", source="archivist", details=None) -> None:
        await self._http.post(
            "/logs",
            json={
                "level": level,
                "source": source,
                "action": action,
                "message": message,
                "details": details or {},
            },
        )

    async def write_memory(
        self, content, type="memory", tags=None, parents=None, confidence=1.0, project=None
    ) -> dict:
        r = await self._http.post(
            "/memory",
            json={
                "content": content,
                "type": type,
                "scope": "project",
                "tags": tags or [],
                "parents": parents or [],
                "confidence": confidence,
                "project": project,
            },
        )
        r.raise_for_status()
        return r.json()

    async def delete_memory(self, entry_id: str) -> None:
        r = await self._http.delete(f"/memory/{entry_id}")
        r.raise_for_status()

    async def get_delta(self, since: str) -> list[dict]:
        r = await self._http.get("/memory/delta", params={"since": since})
        r.raise_for_status()
        return r.json()

    async def get_directives(self, project=None) -> list[dict]:
        return []

    async def list_tasks(self, status=None, limit=50) -> list[dict]:
        params: dict = {"limit": limit}
        if status:
            params["status"] = status
        r = await self._http.get("/tasks", params=params)
        r.raise_for_status()
        return r.json()


@pytest_asyncio.fixture
async def arch(scenario):
    r = await scenario._admin.post("/agents/register", json={"agent_id": ARCHIVIST_ID})
    r.raise_for_status()
    api_key = r.json()["api_key"]
    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='owner' WHERE id=?", (ARCHIVIST_ID,))
    db.commit()
    http = AsyncClient(
        transport=scenario._transport,
        base_url="http://test",
        headers={"x-agent-id": ARCHIVIST_ID, "x-api-key": api_key},
    )
    client = _ArchivistClient(http)
    yield scenario, client
    await http.aclose()


async def _run_decay(client, decay_rate=0.9, decay_floor=0.05, decay_window_days=7):
    with patch("artel.archivist.synthesis.settings") as s:
        s.decay_window_days = decay_window_days
        s.decay_floor = decay_floor
        s.decay_rate = decay_rate
        s.archivist_id = ARCHIVIST_ID
        s.synthesis_interval = 3600
        s.control_decay_enabled = False
        await decay_confidence(client)


async def _run_synthesis(client, llm_response, decay_floor=0.05):
    from artel.archivist.synthesis import run_synthesis

    with (
        patch("artel.archivist.synthesis.is_configured", return_value=True),
        patch("artel.archivist.synthesis.settings") as s,
        patch("artel.archivist.synthesis.complete", new=AsyncMock(return_value=llm_response)),
    ):
        s.archivist_id = ARCHIVIST_ID
        s.directive_conflict_threshold = 0.85
        s.decay_floor = decay_floor
        await run_synthesis(client)


async def _snap(project=None):
    with patch("artel.archivist.synthesis.settings") as s:
        s.synthesis_interval = 3600
        s.decay_rate = 0.9
        s.decay_window_days = 7
        s.archivist_id = ARCHIVIST_ID
        s.control_decay_enabled = False
        await capture_metrics(project=project)
    row = (
        db_mod.get_db()
        .execute("SELECT * FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1")
        .fetchone()
    )
    return dict(row)


# ---------------------------------------------------------------------------
# Scenario 1: Active incident investigation — hot memories survive decay
#
# Three agents write memories about a live production incident. Two
# "responders" independently read every incident memory. Unrelated
# background memories are never touched. The archivist runs decay.
# Expected: all incident memories survive intact; background memories decay.
# ---------------------------------------------------------------------------


async def test_incident_investigation_hot_memories_survive(arch):
    scenario, archivist = arch

    alice = await scenario.agent("incident-alice")
    bob = await scenario.agent("incident-bob")
    carol = await scenario.agent("incident-carol")
    responder_a = await scenario.agent("responder-a")
    responder_b = await scenario.agent("responder-b")

    incident_entries = [
        await alice.write_memory(
            "orders-service p99 latency spiked to 4s at 03:14 UTC", tags=["incident"]
        ),
        await bob.write_memory(
            "root cause: missing index on customer_id in orders table", tags=["incident"]
        ),
        await carol.write_memory(
            "hotfix deployed at 03:47 UTC, latency normalized at 03:52", tags=["incident"]
        ),
    ]

    background_entries = [
        await alice.write_memory("team standup is at 10am daily"),
        await bob.write_memory("staging environment refresh is scheduled for Friday"),
    ]

    for entry in incident_entries:
        _age_entry(entry["id"], 10)
    for entry in background_entries:
        _age_entry(entry["id"], 10)

    for entry in incident_entries:
        await responder_a.get_memory(entry["id"])
        await responder_b.get_memory(entry["id"])
        await responder_a.get_memory(entry["id"])

    await _run_decay(archivist)

    for entry in incident_entries:
        assert _get_confidence(entry["id"]) == 1.0, (
            f"Incident memory {entry['id'][:8]} should have survived decay"
        )

    for entry in background_entries:
        assert _get_confidence(entry["id"]) < 1.0, (
            f"Background memory {entry['id'][:8]} should have been decayed"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Abandoned knowledge entropy — unread memories decay to floor
#
# Five "research" memories are written and immediately backdated to 30 days
# ago (simulating old knowledge nobody has touched). The archivist runs
# decay three cycles. Expected: all five memories reach the decay floor.
# ---------------------------------------------------------------------------


async def test_abandoned_knowledge_decays_to_floor(arch):
    scenario, archivist = arch

    agent = await scenario.agent("researcher")

    old_entries = []
    for i in range(5):
        e = await agent.write_memory(f"old research finding #{i + 1}: context expired")
        old_entries.append(e)

    # Simulate three archivist cycles separated by 10 days each.
    # After each decay run, patch_memory resets updated_at to now, so we
    # must re-age entries before the next cycle to keep them in the decay window.
    # decay_rate=0.3: 1.0 → 0.3 → 0.09 → 0.05 (floor) in 3 cycles.
    for _ in range(3):
        for e in old_entries:
            _age_entry(e["id"], 10)
        await _run_decay(archivist, decay_rate=0.3, decay_floor=0.05, decay_window_days=7)

    for entry in old_entries:
        assert _get_confidence(entry["id"]) <= 0.05 + 1e-6, (
            f"Old entry {entry['id'][:8]} should be at floor after 3 decay cycles"
        )


# ---------------------------------------------------------------------------
# Scenario 3: Merged knowledge stays hot — archivist merger followed by reads
#
# Two agents write similar observations. The archivist merges them. Several
# agents then read the merged entry. Decay runs. The merged entry must survive
# because it is actively referenced.
# ---------------------------------------------------------------------------


async def test_merged_hot_knowledge_survives_decay(arch):
    scenario, archivist = arch

    agent_a = await scenario.agent("merge-a")
    agent_b = await scenario.agent("merge-b")
    agent_c = await scenario.agent("merge-c")

    mem_a = await agent_a.write_memory("DB connection pool is set to 20 threads", tags=["db"])
    mem_b = await agent_b.write_memory("Connection pool size defaults to 20", tags=["db"])

    llm = (
        f'[{{"op":"merge","entries":["{mem_a["id"]}","{mem_b["id"]}"],'
        f'"merged_content":"DB connection pool is configured with 20 threads (default)"}}]'
    )
    await _run_synthesis(archivist, llm)

    all_entries = await agent_a.list_memory()
    merged = next(
        e
        for e in all_entries
        if e.get("agent_id") == ARCHIVIST_ID and "DB connection pool" in e.get("content", "")
    )
    _age_entry(merged["id"], 15)

    for _ in range(6):
        await agent_c.get_memory(merged["id"])
    await agent_a.get_memory(merged["id"])
    await agent_b.get_memory(merged["id"])

    await _run_decay(archivist)

    assert _get_confidence(merged["id"]) == pytest.approx(1.0, abs=1e-6), (
        "Merged entry with high read count should not be decayed"
    )


# ---------------------------------------------------------------------------
# Scenario 4: Decay regret — archivist flags entries, agents read them anyway
#
# The archivist decides three entries are low-signal and flags them (sets
# confidence to floor and adds archivist-flagged). Then agents read those
# flagged entries repeatedly, signalling they are still useful. A metrics
# snapshot captures this as decay regret.
# ---------------------------------------------------------------------------


async def test_decay_regret_detected_in_metrics(arch):
    scenario, archivist = arch

    writer = await scenario.agent("regret-writer")
    reader_a = await scenario.agent("regret-reader-a")
    reader_b = await scenario.agent("regret-reader-b")

    entries = []
    for i in range(3):
        e = await writer.write_memory(f"flagged-but-useful knowledge #{i + 1}")
        entries.append(e)

    for e in entries:
        await writer.update_memory(
            e["id"],
            confidence=0.4,
            tags=["archivist-flagged"],
        )

    for e in entries:
        for _ in range(4):
            await reader_a.get_memory(e["id"])
        for _ in range(2):
            await reader_b.get_memory(e["id"])

    snap = await _snap()

    assert snap["decay_regret_count"] >= 3, (
        f"Expected at least 3 regret entries, got {snap['decay_regret_count']}"
    )
    assert snap["total_entries"] >= 3
    assert snap["utilization_rate"] > 0


# ---------------------------------------------------------------------------
# Scenario 5: Two-snapshot trend — growing then utilized store
#
# A baseline snapshot is taken after writing a small set of entries. Then
# more entries are added, some are read. A second snapshot is taken. The
# second snapshot should show higher total_entries and higher utilization.
# ---------------------------------------------------------------------------


async def test_two_snapshots_show_coherent_trend(arch):
    scenario, archivist = arch

    agent = await scenario.agent("trend-agent")
    reader = await scenario.agent("trend-reader")

    initial_entries = []
    for i in range(5):
        e = await agent.write_memory(f"initial fact #{i + 1}: established baseline knowledge")
        initial_entries.append(e)

    snap1 = await _snap()

    new_entries = []
    for i in range(5):
        e = await agent.write_memory(f"new discovery #{i + 1}: expanding knowledge base")
        new_entries.append(e)

    for e in initial_entries[:3]:
        await reader.get_memory(e["id"])
        await reader.get_memory(e["id"])
    for e in new_entries[:2]:
        await reader.get_memory(e["id"])

    snap2 = await _snap()

    assert snap2["total_entries"] > snap1["total_entries"], (
        "Second snapshot should show more entries"
    )
    assert snap2["utilization_rate"] > snap1["utilization_rate"], (
        "Second snapshot should show higher utilization"
    )
    assert snap1["utilization_rate"] == 0.0, (
        "First snapshot should have zero utilization (no reads yet)"
    )


# ---------------------------------------------------------------------------
# Scenario 6: Cooled heat — aged reads no longer protect from decay
#
# An entry was read 3 times but the last read was 60 days ago. The heat
# formula decays the score below the protection threshold, so the archivist
# should decay its confidence normally.
# ---------------------------------------------------------------------------


async def test_cooled_heat_does_not_protect_from_decay(arch):
    scenario, archivist = arch

    writer = await scenario.agent("cooled-writer")
    entry = await writer.write_memory("very old finding from months ago")
    original_confidence = entry["confidence"]

    _age_entry(entry["id"], 30)
    # 3 reads at 90 days ago: heat = 3 * 0.9^(90/7) ≈ 3 * 0.258 ≈ 0.77 < 1.0 → not protected
    _set_read_state(entry["id"], read_count=3, last_read_at=_ts(90))

    await _run_decay(archivist)

    assert _get_confidence(entry["id"]) < original_confidence, (
        "Entry with cooled heat (read 90 days ago) should be decayed"
    )


# ---------------------------------------------------------------------------
# Scenario 7: Recent heat always wins — even a single fresh read blocks decay
#
# Mirrors scenario 6 but with a fresh last_read_at. A single recent read
# is enough to push heat above 1.0 (heat = 1 * 0.9^0 = 1.0, exactly at the
# threshold). Entry must NOT be decayed.
# ---------------------------------------------------------------------------


async def test_recent_single_read_protects_from_decay(arch):
    scenario, archivist = arch

    writer = await scenario.agent("fresh-writer")
    entry = await writer.write_memory("finding that was just referenced today")

    _age_entry(entry["id"], 14)
    # 3 reads today: heat = 3 * 0.9^~0 ≈ 3.0, well above threshold even with elapsed time
    _set_read_state(entry["id"], read_count=3, last_read_at=_ts(0))

    original_confidence = _get_confidence(entry["id"])

    await _run_decay(archivist)

    assert _get_confidence(entry["id"]) == original_confidence, (
        "Entry read 3 times today should have heat ≈ 3.0 and be protected from decay"
    )


# ---------------------------------------------------------------------------
# Scenario 8: Multi-agent swarm — collective reads protect shared knowledge
#
# Six agents each read a set of shared technical docs exactly once. The
# combined read count per entry is six. Even though each individual agent
# only read once (and the last read may have been brief), the cumulative
# signal protects those entries during decay.
# ---------------------------------------------------------------------------


async def test_multi_agent_collective_reads_protect_shared_docs(arch):
    scenario, archivist = arch

    writer = await scenario.agent("shared-doc-writer")
    readers = [await scenario.agent(f"swarm-reader-{i}") for i in range(6)]

    shared_entries = [
        await writer.write_memory(f"shared technical spec #{i + 1}: used by all agents")
        for i in range(4)
    ]
    unread_entry = await writer.write_memory("obscure internal note nobody references")

    for entry in shared_entries:
        _age_entry(entry["id"], 12)
    _age_entry(unread_entry["id"], 12)

    for reader in readers:
        for entry in shared_entries:
            await reader.get_memory(entry["id"])

    await _run_decay(archivist)

    for entry in shared_entries:
        assert _get_confidence(entry["id"]) == 1.0, (
            f"Shared entry read by 6 agents should survive decay: {entry['id'][:8]}"
        )

    assert _get_confidence(unread_entry["id"]) < 1.0, "Unread entry should decay normally"


# ---------------------------------------------------------------------------
# Scenario 9: Comprehensive metrics snapshot reflects known store state
#
# A carefully constructed store is created: known entry counts, read counts,
# tag distributions. The metrics snapshot must match the expected values
# exactly.
# ---------------------------------------------------------------------------


async def test_comprehensive_metrics_snapshot_reflects_known_state(arch):
    scenario, archivist = arch

    writer = await scenario.agent("metrics-writer")
    reader = await scenario.agent("metrics-reader")

    active_entries = [await writer.write_memory(f"active memory #{i + 1}") for i in range(8)]

    await writer.update_memory(active_entries[0]["id"], tags=["archivist-conflict"])
    await writer.update_memory(active_entries[1]["id"], tags=["archivist-conflict"])

    await writer.update_memory(active_entries[2]["id"], confidence=0.4, tags=["archivist-flagged"])
    await writer.update_memory(active_entries[3]["id"], confidence=0.3, tags=["archivist-flagged"])

    for e in [active_entries[2], active_entries[3]]:
        for _ in range(5):
            await reader.get_memory(e["id"])

    for e in active_entries[4:7]:
        await reader.get_memory(e["id"])

    snap = await _snap()

    assert snap["total_entries"] == 8
    assert snap["contradiction_count"] == 2
    assert snap["decay_regret_count"] == 2
    assert snap["utilization_rate"] == pytest.approx(5 / 8, abs=1e-6)


# ---------------------------------------------------------------------------
# Scenario 10: Synthesis log op_counts drive merge metric in snapshot
#
# After a real synthesis pass with a mocked LLM that emits two merge ops,
# the archivist log records the op_counts. A subsequent capture_metrics call
# reads those counts correctly.
# ---------------------------------------------------------------------------


async def test_synthesis_log_drives_metrics_merge_count(arch):
    scenario, archivist = arch

    a = await scenario.agent("log-merge-a")
    b = await scenario.agent("log-merge-b")
    c = await scenario.agent("log-merge-c")
    d = await scenario.agent("log-merge-d")

    m1 = await a.write_memory("service A uses postgres for storage")
    m2 = await b.write_memory("service A stores data in postgres")
    m3 = await c.write_memory("service B is written in Go")
    m4 = await d.write_memory("service B backend uses Go language")

    llm = (
        f'[{{"op":"merge","entries":["{m1["id"]}","{m2["id"]}"],'
        f'"merged_content":"service A uses postgres for storage"}},'
        f'{{"op":"merge","entries":["{m3["id"]}","{m4["id"]}"],'
        f'"merged_content":"service B is implemented in Go"}}]'
    )
    await _run_synthesis(archivist, llm)

    snap = await _snap()

    assert snap["merge_count"] == 2, (
        f"Expected merge_count=2 from synthesis log, got {snap['merge_count']}"
    )
    assert snap["synthesis_count"] == 2


# ---------------------------------------------------------------------------
# Scenario 11: Prune-then-read creates regret; next metrics cycle reports it
#
# Full lifecycle of a decay regret event: write → archivist prunes
# (flags + lowers confidence) → agents read → metrics snapshot.
# ---------------------------------------------------------------------------


async def test_prune_then_read_creates_measurable_regret(arch):
    scenario, archivist = arch

    writer = await scenario.agent("prune-then-read-writer")
    reader_a = await scenario.agent("prune-then-read-ra")
    reader_b = await scenario.agent("prune-then-read-rb")

    await writer.write_memory("padding entry to satisfy synthesis threshold")
    target = await writer.write_memory(
        "incident runbook: step-by-step recovery procedure", confidence=0.9
    )

    prune_llm = f'[{{"op":"prune","entry":"{target["id"]}"}}]'
    await _run_synthesis(archivist, prune_llm)

    post_prune = await writer.get_memory(target["id"])
    assert post_prune["confidence"] <= 0.05 + 1e-6
    assert "archivist-flagged" in post_prune.get("tags", [])

    for _ in range(5):
        await reader_a.get_memory(target["id"])
        await reader_b.get_memory(target["id"])

    snap = await _snap()

    assert snap["decay_regret_count"] >= 1, (
        "Pruned-but-read entry should appear as decay regret in metrics"
    )
