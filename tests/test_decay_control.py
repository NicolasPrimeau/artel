import json
from unittest.mock import AsyncMock

import pytest

from artel.archivist import synthesis
from artel.archivist.config import settings as arch_settings


def _seed(db, entry_id, **overrides):
    cols = {
        "type": "memory",
        "agent_id": "a1",
        "content": "x",
        "confidence": 1.0,
        "tags": "[]",
        "read_count": 0,
    }
    cols.update(overrides)
    names = ",".join(cols)
    placeholders = ",".join("?" * len(cols))
    db.execute(
        f"INSERT INTO memory (id,{names}) VALUES (?,{placeholders})",
        (entry_id, *cols.values()),
    )


def _regret_event(db, memory_id, confidence=0.4, agent_id="a2"):
    """A read of an already-decayed entry — the controller's sensor is this flow."""
    db.execute(
        "INSERT INTO decay_regret_events (id, memory_id, agent_id, project, confidence)"
        " VALUES (?,?,?,?,?)",
        (f"ev-{memory_id}-{confidence}-{agent_id}", memory_id, agent_id, None, confidence),
    )


@pytest.mark.asyncio
async def test_capture_metrics_closes_decay_loop(client):
    from artel.store.db import get_db

    db = get_db()
    for i in range(3):
        _seed(db, f"r{i}", confidence=0.5, read_count=1, tags='["archivist-flagged"]')
        _regret_event(db, f"r{i}")
    for i in range(4):
        _seed(db, f"s{i}", agent_id=arch_settings.archivist_id, read_count=(1 if i < 3 else 0))
    db.commit()

    await synthesis.capture_metrics()

    row = db.execute("SELECT * FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1").fetchone()
    assert row["decay_regret_count"] == 3, "the controller reads the event flow"
    assert row["prune_regret_count"] == 3, "the pruned-then-read stock is recorded separately"
    assert abs(row["synthesis_uptake_rate"] - 0.75) < 1e-9
    params = json.loads(row["params"])
    assert params["decay_rate"] > params["decay_rate_bias"]
    assert synthesis.controlled_decay_rate() > arch_settings.decay_rate


@pytest.mark.asyncio
async def test_decay_confidence_applies_controlled_rate(client):
    from artel.store.db import get_db

    db = get_db()
    for i in range(3):
        _seed(db, f"r{i}", confidence=0.5, read_count=1, tags='["archivist-flagged"]')
        _regret_event(db, f"r{i}")
    db.commit()

    await synthesis.capture_metrics()
    rate = synthesis.controlled_decay_rate()
    assert rate > arch_settings.decay_rate

    mock = AsyncMock()
    mock.list_entries.return_value = [
        {"id": "d1", "confidence": 0.8, "type": "memory", "origin": None}
    ]
    await synthesis.decay_confidence(mock)

    mock.patch_memory.assert_awaited_once()
    _, kwargs = mock.patch_memory.call_args
    assert abs(kwargs["confidence"] - 0.8 * rate) < 1e-9


@pytest.mark.asyncio
async def test_zero_regret_holds_rate_at_bias(client):
    from artel.store.db import get_db

    db = get_db()
    _seed(db, "clean", confidence=0.9, read_count=1)
    db.commit()

    await synthesis.capture_metrics()

    row = db.execute("SELECT * FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1").fetchone()
    assert row["decay_regret_count"] == 0
    assert abs(synthesis.controlled_decay_rate() - arch_settings.decay_rate) < 1e-9


@pytest.mark.asyncio
async def test_control_disabled_falls_back_to_static_rate(client, monkeypatch):
    from artel.store.db import get_db

    monkeypatch.setattr(arch_settings, "control_decay_enabled", False)
    db = get_db()
    for i in range(5):
        _seed(db, f"r{i}", confidence=0.5, read_count=1, tags='["archivist-flagged"]')
    db.commit()

    await synthesis.capture_metrics()

    assert synthesis.controlled_decay_rate() == arch_settings.decay_rate
    row = db.execute(
        "SELECT params FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    assert json.loads(row["params"])["decay_rate"] == arch_settings.decay_rate


@pytest.mark.asyncio
async def test_regret_flow_returns_to_zero_when_nothing_is_read(client):
    """The point of the change: a quiet cycle must read zero.

    The previous sensor counted a standing population pinned at the confidence
    floor, so it could only ratchet upward and never reached its setpoint —
    the controller sat saturated against an error it could not remove.
    """
    from artel.store.db import get_db

    db = get_db()
    for i in range(3):
        _seed(db, f"r{i}", confidence=0.5, read_count=1, tags='["archivist-flagged"]')
        _regret_event(db, f"r{i}")
    db.commit()

    await synthesis.capture_metrics()
    first = db.execute(
        "SELECT * FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    assert first["decay_regret_count"] == 3

    # Second cycle: the same flagged entries still exist, but nobody read one.
    await synthesis.capture_metrics()
    second = db.execute(
        "SELECT * FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    assert second["decay_regret_count"] == 0, "flow decays to zero"
    assert second["prune_regret_count"] == 3, "stock persists — that is why it cannot be the sensor"


@pytest.mark.asyncio
async def test_prune_regret_is_fed_back_to_the_pass_that_prunes(client, monkeypatch):
    """The stock is feedback on the prune policy, so it must reach the prune prompt.

    Pointing the decay controller at it was the original mistake: a prune sets
    confidence to the floor in one step, so no decay rate can move that population.
    """
    from artel.store.db import get_db

    db = get_db()
    for i in range(2):
        _seed(db, f"p{i}", confidence=0.4, read_count=2, tags='["archivist-flagged"]')
    db.commit()

    monkeypatch.setattr(synthesis, "is_configured", lambda: True)
    monkeypatch.setattr(synthesis, "_read_synthesis_cursor", lambda: "2020-01-01T00:00:00.000Z")
    monkeypatch.setattr(synthesis, "_write_synthesis_cursor", lambda v: None)

    seen: list[str] = []

    async def fake_ops(system, user, label):
        seen.append(system)
        return []

    monkeypatch.setattr(synthesis, "_llm_ops_pass", fake_ops)

    c = AsyncMock()
    c.get_directives.return_value = []
    c.get_delta.return_value = [
        {
            "id": f"m{i}",
            "agent_id": "a",
            "type": "memory",
            "content": "c",
            "confidence": 1.0,
            "tags": [],
            "origin": None,
        }
        for i in range(3)
    ]
    c.list_tasks.return_value = []

    await synthesis.run_synthesis(c)

    assert seen, "the cleanup pass should have run"
    assert "CALIBRATION: 2 entries" in seen[0]
    assert "Prune more conservatively" in seen[0]


@pytest.mark.asyncio
async def test_no_calibration_noise_when_nothing_was_regretted(client, monkeypatch):
    monkeypatch.setattr(synthesis, "is_configured", lambda: True)
    monkeypatch.setattr(synthesis, "_read_synthesis_cursor", lambda: "2020-01-01T00:00:00.000Z")
    monkeypatch.setattr(synthesis, "_write_synthesis_cursor", lambda v: None)

    seen: list[str] = []

    async def fake_ops(system, user, label):
        seen.append(system)
        return []

    monkeypatch.setattr(synthesis, "_llm_ops_pass", fake_ops)

    c = AsyncMock()
    c.get_directives.return_value = []
    c.get_delta.return_value = [
        {
            "id": f"m{i}",
            "agent_id": "a",
            "type": "memory",
            "content": "c",
            "confidence": 1.0,
            "tags": [],
            "origin": None,
        }
        for i in range(3)
    ]
    c.list_tasks.return_value = []

    await synthesis.run_synthesis(c)

    assert seen
    assert "CALIBRATION" not in seen[0]
