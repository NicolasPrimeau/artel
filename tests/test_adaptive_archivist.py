"""
Tests for adaptive archivist features:
  - Read-count tracking on GET /memory/:id
  - Heat-aware decay (LFU with aging)
  - capture_metrics snapshot computation
  - archivist_metrics schema + read_count/last_read_at columns
"""

import json
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artel.archivist import synthesis
from artel.archivist.synthesis import capture_metrics, decay_confidence
from tests.conftest import HEADERS, HEADERS2, TEST_AGENT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(offset_days: float = 0.0) -> str:
    dt = datetime.now(UTC) - timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _insert_memory(
    db,
    *,
    entry_id=None,
    confidence=1.0,
    deleted=False,
    type_="memory",
    read_count=0,
    last_read_at=None,
    tags=None,
    created_at=None,
    deleted_at=None,
    origin=None,
):
    eid = entry_id or secrets.token_hex(8)
    now = _ts()
    tags_json = json.dumps(tags or [])
    db.execute(
        """INSERT INTO memory
           (id, type, agent_id, content, confidence, tags, read_count, last_read_at,
            created_at, updated_at, deleted_at, origin)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            eid,
            type_,
            TEST_AGENT,
            f"content of {eid}",
            confidence,
            tags_json,
            read_count,
            last_read_at,
            created_at or now,
            now,
            _ts() if deleted else deleted_at,
            origin,
        ),
    )
    db.commit()
    return eid


def _insert_arch_log(db, action, details):
    lid = secrets.token_hex(8)
    db.execute(
        """INSERT INTO archivist_logs (id, level, source, action, message, details)
           VALUES (?,?,?,?,?,?)""",
        (lid, "info", "archivist", action, "test log", json.dumps(details)),
    )
    db.commit()


@pytest.fixture
def raw_db(tmp_path, monkeypatch):
    """In-process DB singleton for archivist unit tests."""
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    db_mod._conn = None
    test_db_path = str(tmp_path / "adaptive.db")
    monkeypatch.setattr(cfg_mod.settings, "db_path", test_db_path)

    conn = db_mod.get_db(test_db_path)
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (TEST_AGENT, "testkey"))
    conn.commit()
    yield conn
    conn.close()
    db_mod._conn = None


# ---------------------------------------------------------------------------
# 1. Read-count tracking via HTTP API
# ---------------------------------------------------------------------------


class TestReadCountTracking:
    async def test_initial_read_count_is_zero(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "hello",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        assert r.status_code == 201
        eid = r.json()["id"]

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 0

    async def test_get_by_id_increments_read_count(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "trackme",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        await client.get(f"/memory/{eid}", headers=HEADERS)

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 1

    async def test_multiple_gets_accumulate_read_count(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "multi",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        for _ in range(5):
            await client.get(f"/memory/{eid}", headers=HEADERS)

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 5

    async def test_get_by_id_sets_last_read_at(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "timestamp test",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        import artel.store.db as db_mod

        row_before = db_mod._conn.execute(
            "SELECT last_read_at FROM memory WHERE id=?", (eid,)
        ).fetchone()
        assert row_before["last_read_at"] is None

        await client.get(f"/memory/{eid}", headers=HEADERS)

        row_after = db_mod._conn.execute(
            "SELECT last_read_at FROM memory WHERE id=?", (eid,)
        ).fetchone()
        assert row_after["last_read_at"] is not None

    async def test_list_does_not_increment_read_count(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "list test",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        await client.get("/memory", headers=HEADERS)

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 0

    async def test_search_hit_increments_read_count(self, client):
        # agents consume content straight from search responses — a hit IS a read,
        # and the heat protects the entry from decay
        r = await client.post(
            "/memory",
            json={
                "content": "search test unique",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        await client.get("/memory/search", params={"q": "search test unique"}, headers=HEADERS)

        import artel.store.db as db_mod

        row = db_mod._conn.execute(
            "SELECT read_count, last_read_at FROM memory WHERE id=?", (eid,)
        ).fetchone()
        assert row["read_count"] == 1
        assert row["last_read_at"] is not None

    async def test_delta_does_not_increment_read_count(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "delta test",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        await client.get("/memory/delta", params={"since": "2000-01-01T00:00:00Z"}, headers=HEADERS)

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 0

    async def test_different_agents_each_increment_count(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "shared read",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        await client.get(f"/memory/{eid}", headers=HEADERS)
        await client.get(f"/memory/{eid}", headers=HEADERS2)

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 2

    async def test_get_by_prefix_increments_read_count(self, client):
        r = await client.post(
            "/memory",
            json={
                "content": "prefix read",
                "type": "memory",
                "scope": "project",
                "tags": [],
                "parents": [],
                "confidence": 1.0,
            },
            headers=HEADERS,
        )
        eid = r.json()["id"]

        prefix = eid[:8]
        r2 = await client.get(f"/memory/{prefix}", headers=HEADERS)
        assert r2.status_code == 200

        import artel.store.db as db_mod

        row = db_mod._conn.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 1


# ---------------------------------------------------------------------------
# 2. Heat-aware decay
# ---------------------------------------------------------------------------


class TestHeatAwareDecay:
    def _make_entry(self, entry_id, confidence=0.8, read_count=0, last_read_at=None):
        return {
            "id": entry_id,
            "agent_id": "agent-x",
            "type": "memory",
            "content": f"entry {entry_id}",
            "confidence": confidence,
            "origin": None,
        }

    def _make_client(self):
        client = MagicMock()
        client.patch_memory = AsyncMock(return_value={})
        client.log = AsyncMock()
        client.list_entries = AsyncMock()
        return client

    async def test_zero_reads_entry_is_decayed(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, read_count=0)
        entry = self._make_entry(eid, confidence=0.9)
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_called_once()
        assert client.patch_memory.call_args.args[0] == eid

    async def test_high_heat_entry_is_skipped(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, read_count=10, last_read_at=_ts(0))
        entry = self._make_entry(eid, confidence=0.9)
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()

    async def test_moderately_read_but_recent_is_skipped(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, read_count=3, last_read_at=_ts(3))
        entry = self._make_entry(eid, confidence=0.9)
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()

    async def test_old_single_read_is_not_protected(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, read_count=1, last_read_at=_ts(60))
        entry = self._make_entry(eid, confidence=0.9)
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_called_once_with(eid, confidence=pytest.approx(0.81, abs=1e-3))

    async def test_heat_exactly_at_threshold_is_protected(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, read_count=2, last_read_at=_ts(14))
        entry = self._make_entry(eid, confidence=0.9)
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        assert client.patch_memory.call_count == 0

    async def test_at_floor_confidence_skipped_regardless_of_heat(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.05, read_count=0)
        entry = self._make_entry(eid, confidence=0.05)
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()

    async def test_heat_skip_is_logged(self, raw_db):
        eid_hot = _insert_memory(raw_db, confidence=0.9, read_count=10, last_read_at=_ts(0))
        eid_cold = _insert_memory(raw_db, confidence=0.9, read_count=0)
        client = self._make_client()
        client.list_entries = AsyncMock(
            return_value=[
                self._make_entry(eid_hot, confidence=0.9),
                self._make_entry(eid_cold, confidence=0.9),
            ]
        )

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.log.assert_called_once()
        log_details = client.log.call_args.kwargs.get("details", {})
        assert log_details["heat_skipped"] == 1
        assert log_details["decayed"] == 1

    async def test_multiple_hot_entries_all_skipped(self, raw_db):
        eids = [
            _insert_memory(raw_db, confidence=0.8, read_count=5, last_read_at=_ts(1))
            for _ in range(4)
        ]
        client = self._make_client()
        client.list_entries = AsyncMock(
            return_value=[self._make_entry(eid, confidence=0.8) for eid in eids]
        )

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()

    async def test_archivist_authored_entries_also_decay(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, read_count=0)
        archivist_entry = {
            "id": eid,
            "agent_id": "archivist",
            "type": "memory",
            "content": "archivist wrote this",
            "confidence": 0.9,
            "origin": None,
        }
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[archivist_entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_called_once()
        assert client.patch_memory.call_args.kwargs["confidence"] == 0.9 * 0.9

    async def test_directive_entries_never_decayed(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, type_="directive")
        directive_entry = {
            "id": eid,
            "agent_id": TEST_AGENT,
            "type": "directive",
            "content": "standing directive",
            "confidence": 0.9,
            "origin": None,
        }
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[directive_entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()

    async def test_doc_entries_never_decayed(self, raw_db):
        eid = _insert_memory(raw_db, confidence=0.9, type_="doc")
        doc_entry = {
            "id": eid,
            "agent_id": TEST_AGENT,
            "type": "doc",
            "content": "canonical doc",
            "confidence": 0.9,
            "origin": None,
        }
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[doc_entry])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()

    async def test_empty_entry_list_no_side_effects(self, raw_db):
        client = self._make_client()
        client.list_entries = AsyncMock(return_value=[])

        with patch("artel.archivist.synthesis.settings") as s:
            s.decay_window_days = 7
            s.decay_floor = 0.05
            s.decay_rate = 0.9
            s.archivist_id = "archivist"
            s.synthesis_interval = 3600
            await decay_confidence(client)

        client.patch_memory.assert_not_called()
        client.log.assert_not_called()


# ---------------------------------------------------------------------------
# 3. capture_metrics
# ---------------------------------------------------------------------------


class TestCaptureMetrics:
    async def test_inserts_row_into_archivist_metrics(self, raw_db):
        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT COUNT(*) FROM archivist_metrics").fetchone()[0]
        assert row == 1

    async def test_total_entries_counts_active_non_directives(self, raw_db):
        _insert_memory(raw_db)
        _insert_memory(raw_db)
        _insert_memory(raw_db, deleted=True)
        _insert_memory(raw_db, type_="directive")

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT total_entries FROM archivist_metrics").fetchone()
        assert row[0] == 2

    async def test_utilization_rate_zero_when_no_reads(self, raw_db):
        _insert_memory(raw_db, read_count=0)
        _insert_memory(raw_db, read_count=0)

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT utilization_rate FROM archivist_metrics").fetchone()
        assert row[0] == 0.0

    async def test_utilization_rate_partial(self, raw_db):
        _insert_memory(raw_db, read_count=3)
        _insert_memory(raw_db, read_count=0)
        _insert_memory(raw_db, read_count=0)
        _insert_memory(raw_db, read_count=1)

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT utilization_rate FROM archivist_metrics").fetchone()
        assert row[0] == pytest.approx(0.5, abs=1e-6)

    async def test_utilization_rate_full(self, raw_db):
        for _ in range(4):
            _insert_memory(raw_db, read_count=2)

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT utilization_rate FROM archivist_metrics").fetchone()
        assert row[0] == pytest.approx(1.0, abs=1e-6)

    async def test_decay_regret_count_identifies_flagged_hot_entries(self, raw_db):
        _insert_memory(raw_db, read_count=5, confidence=0.4, tags=["archivist-flagged"])
        _insert_memory(raw_db, read_count=0, confidence=0.4, tags=["archivist-flagged"])
        _insert_memory(raw_db, read_count=3, confidence=0.9, tags=["archivist-flagged"])
        _insert_memory(raw_db, read_count=2, confidence=0.3)

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT decay_regret_count FROM archivist_metrics").fetchone()
        assert row[0] == 1

    async def test_contradiction_count_counts_conflict_tagged_entries(self, raw_db):
        _insert_memory(raw_db, tags=["archivist-conflict"])
        _insert_memory(raw_db, tags=["archivist-conflict", "other"])
        _insert_memory(raw_db, tags=["other"])

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT contradiction_count FROM archivist_metrics").fetchone()
        assert row[0] == 2

    async def test_contradiction_count_excludes_deleted(self, raw_db):
        _insert_memory(raw_db, tags=["archivist-conflict"])
        _insert_memory(raw_db, deleted=True, tags=["archivist-conflict"])

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT contradiction_count FROM archivist_metrics").fetchone()
        assert row[0] == 1

    async def test_net_growth_positive_when_more_created(self, raw_db):
        recent = _ts(1 / 48)
        for _ in range(3):
            _insert_memory(raw_db, created_at=recent)
        # old entry created long ago, just deleted now
        _insert_memory(raw_db, created_at=_ts(30), deleted=True)

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT net_growth FROM archivist_metrics").fetchone()
        assert row[0] == 2

    async def test_net_growth_negative_when_more_deleted(self, raw_db):
        recent = _ts(1 / 48)
        _insert_memory(raw_db, created_at=recent)
        # 4 old entries created long ago, all deleted now
        for _ in range(4):
            _insert_memory(raw_db, created_at=_ts(30), deleted=True)

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT net_growth FROM archivist_metrics").fetchone()
        assert row[0] == -3

    async def test_merge_count_read_from_synthesis_log(self, raw_db):
        _insert_arch_log(
            raw_db,
            "synthesis",
            {"ops": 5, "op_counts": {"merge": 3, "prune": 2}, "op_types": ["merge", "prune"]},
        )

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT merge_count FROM archivist_metrics").fetchone()
        assert row[0] == 3

    async def test_decay_count_read_from_decay_log(self, raw_db):
        _insert_arch_log(raw_db, "decay", {"decayed": 7, "candidates": 10, "heat_skipped": 3})

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT decay_count FROM archivist_metrics").fetchone()
        assert row[0] == 7

    async def test_synthesis_count_sums_all_ops_from_synthesis_logs(self, raw_db):
        _insert_arch_log(raw_db, "synthesis", {"ops": 4, "op_counts": {"merge": 2, "prune": 2}})
        _insert_arch_log(raw_db, "synthesis", {"ops": 6, "op_counts": {"promote": 6}})

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT synthesis_count FROM archivist_metrics").fetchone()
        assert row[0] == 10

    async def test_params_json_contains_current_settings(self, raw_db):
        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 7200
            s.decay_rate = 0.85
            s.decay_window_days = 14
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute("SELECT params FROM archivist_metrics").fetchone()
        params = json.loads(row[0])
        assert params["decay_rate"] == 0.85
        assert params["decay_window_days"] == 14
        assert params["synthesis_interval"] == 7200

    async def test_empty_db_no_crash(self, raw_db):
        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute(
            "SELECT total_entries, utilization_rate FROM archivist_metrics"
        ).fetchone()
        assert row[0] == 0
        assert row[1] == 0.0

    async def test_multiple_calls_produce_multiple_rows(self, raw_db):
        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()
            await capture_metrics()
            await capture_metrics()

        count = raw_db.execute("SELECT COUNT(*) FROM archivist_metrics").fetchone()[0]
        assert count == 3

    async def test_old_logs_outside_cycle_window_excluded(self, raw_db):
        old_ts = _ts(10)
        lid = secrets.token_hex(8)
        raw_db.execute(
            "INSERT INTO archivist_logs (id, created_at, level, source, action, message, details) VALUES (?,?,?,?,?,?,?)",
            (
                lid,
                old_ts,
                "info",
                "archivist",
                "synthesis",
                "old pass",
                json.dumps({"ops": 99, "op_counts": {"merge": 99}}),
            ),
        )
        raw_db.commit()

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics()

        row = raw_db.execute(
            "SELECT merge_count, synthesis_count FROM archivist_metrics"
        ).fetchone()
        assert row[0] == 0
        assert row[1] == 0

    async def test_metrics_for_specific_project(self, raw_db):
        raw_db.execute(
            "INSERT INTO project_members (project_id, agent_id) VALUES (?,?)",
            ("proj-a", TEST_AGENT),
        )
        raw_db.commit()
        eid1 = secrets.token_hex(8)
        eid2 = secrets.token_hex(8)
        now = _ts()
        raw_db.execute(
            "INSERT INTO memory (id, type, agent_id, project, content, confidence, tags, read_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid1, "memory", TEST_AGENT, "proj-a", "in proj", 1.0, "[]", 5, now, now),
        )
        raw_db.execute(
            "INSERT INTO memory (id, type, agent_id, project, content, confidence, tags, read_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid2, "memory", TEST_AGENT, None, "global", 1.0, "[]", 0, now, now),
        )
        raw_db.commit()

        with patch("artel.archivist.synthesis.settings") as s:
            s.synthesis_interval = 3600
            s.decay_rate = 0.9
            s.decay_window_days = 7
            s.archivist_id = "archivist"
            s.control_decay_enabled = False
            await capture_metrics(project="proj-a")

        row = raw_db.execute(
            "SELECT total_entries, utilization_rate FROM archivist_metrics"
        ).fetchone()
        assert row[0] == 1
        assert row[1] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. Schema and migrations
# ---------------------------------------------------------------------------


class TestSchemaAndMigrations:
    def test_memory_has_read_count_column(self, raw_db):
        cols = {r[1] for r in raw_db.execute("PRAGMA table_info(memory)").fetchall()}
        assert "read_count" in cols

    def test_memory_has_last_read_at_column(self, raw_db):
        cols = {r[1] for r in raw_db.execute("PRAGMA table_info(memory)").fetchall()}
        assert "last_read_at" in cols

    def test_read_count_defaults_to_zero(self, raw_db):
        eid = _insert_memory(raw_db)
        row = raw_db.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 0

    def test_last_read_at_defaults_to_null(self, raw_db):
        eid = _insert_memory(raw_db)
        row = raw_db.execute("SELECT last_read_at FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["last_read_at"] is None

    def test_archivist_metrics_table_exists(self, raw_db):
        tables = {
            r[0]
            for r in raw_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "archivist_metrics" in tables

    def test_archivist_metrics_has_expected_columns(self, raw_db):
        cols = {r[1] for r in raw_db.execute("PRAGMA table_info(archivist_metrics)").fetchall()}
        expected = {
            "id",
            "captured_at",
            "project",
            "total_entries",
            "utilization_rate",
            "decay_regret_count",
            "synthesis_count",
            "synthesis_uptake_rate",
            "contradiction_count",
            "net_growth",
            "merge_count",
            "decay_count",
            "params",
        }
        assert expected <= cols

    def test_archivist_metrics_index_exists(self, raw_db):
        indexes = {
            r[1]
            for r in raw_db.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='archivist_metrics'"
            ).fetchall()
        }
        assert "idx_arch_metrics_captured" in indexes

    def test_memory_read_count_index_not_required_but_insert_works(self, raw_db):
        eid = _insert_memory(raw_db, read_count=42)
        row = raw_db.execute("SELECT read_count FROM memory WHERE id=?", (eid,)).fetchone()
        assert row["read_count"] == 42


# ---------------------------------------------------------------------------
# 5. Synthesis log op_counts
# ---------------------------------------------------------------------------


class TestSynthesisLogOpCounts:
    """run_synthesis logs op_counts per type so capture_metrics can read them."""

    async def test_synthesis_log_includes_op_counts(self):
        entries = [
            {
                "id": "e1",
                "agent_id": "a",
                "type": "memory",
                "content": "x",
                "tags": [],
                "confidence": 1.0,
                "origin": None,
            },
            {
                "id": "e2",
                "agent_id": "b",
                "type": "memory",
                "content": "y",
                "tags": [],
                "confidence": 1.0,
                "origin": None,
            },
        ]
        llm_response = '[{"op": "merge", "entries": ["e1", "e2"], "merged_content": "merged xy"}]'

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})
        client.log = AsyncMock()

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as s,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            s.archivist_id = "archivist"
            s.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        client.log.assert_called()
        log_kwargs = client.log.call_args.kwargs
        details = log_kwargs.get("details", {})
        assert "op_counts" in details
        assert details["op_counts"].get("merge") == 1

    async def test_synthesis_log_op_counts_multiple_types(self):
        entries = [
            {
                "id": "e1",
                "agent_id": "a",
                "type": "memory",
                "content": "x",
                "tags": [],
                "confidence": 1.0,
                "origin": None,
            },
            {
                "id": "e2",
                "agent_id": "b",
                "type": "memory",
                "content": "y",
                "tags": [],
                "confidence": 1.0,
                "origin": None,
            },
        ]
        llm_response = '[{"op": "promote", "entry": "e1"}, {"op": "promote", "entry": "e2"}, {"op": "tag", "entry": "e1", "add_tags": ["x"]}]'

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={"tags": []})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})
        client.log = AsyncMock()
        client.get_memory = AsyncMock(return_value={"tags": []})

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as s,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            s.archivist_id = "archivist"
            s.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        log_kwargs = client.log.call_args.kwargs
        details = log_kwargs.get("details", {})
        assert details["op_counts"].get("promote") == 2
        assert details["op_counts"].get("tag") == 1
        assert details["ops"] == 3

    async def test_synthesis_log_empty_ops_has_empty_op_counts(self):
        entries = [
            {
                "id": "e1",
                "agent_id": "a",
                "type": "memory",
                "content": "x",
                "tags": [],
                "confidence": 1.0,
                "origin": None,
            },
            {
                "id": "e2",
                "agent_id": "b",
                "type": "memory",
                "content": "y",
                "tags": [],
                "confidence": 1.0,
                "origin": None,
            },
        ]
        llm_response = "[]"

        client = MagicMock()
        client.get_directives = AsyncMock(return_value=[])
        client.get_delta = AsyncMock(return_value=entries)
        client.list_tasks = AsyncMock(return_value=[])
        client.patch_memory = AsyncMock(return_value={})
        client.write_memory = AsyncMock(return_value={"id": "new"})
        client.delete_memory = AsyncMock()
        client.create_task = AsyncMock(return_value={"id": "t"})
        client.log = AsyncMock()

        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.settings") as s,
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_response)),
        ):
            s.archivist_id = "archivist"
            s.directive_conflict_threshold = 0.85
            await synthesis.run_synthesis(client)

        log_kwargs = client.log.call_args.kwargs
        details = log_kwargs.get("details", {})
        assert details["op_counts"] == {}
        assert details["ops"] == 0
