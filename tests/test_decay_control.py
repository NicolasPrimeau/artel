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


@pytest.mark.asyncio
async def test_capture_metrics_closes_decay_loop(client):
    from artel.store.db import get_db

    db = get_db()
    for i in range(3):
        _seed(db, f"r{i}", confidence=0.5, read_count=1, tags='["archivist-flagged"]')
    for i in range(4):
        _seed(db, f"s{i}", agent_id=arch_settings.archivist_id, read_count=(1 if i < 3 else 0))
    db.commit()

    await synthesis.capture_metrics()

    row = db.execute("SELECT * FROM archivist_metrics ORDER BY captured_at DESC LIMIT 1").fetchone()
    assert row["decay_regret_count"] == 3
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
