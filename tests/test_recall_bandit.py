import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import HEADERS


@pytest.fixture
def enable_bandit(monkeypatch):
    import artel.server.config as cfg

    monkeypatch.setattr(cfg.settings, "recall_bandit_enabled", True)


@pytest.fixture(autouse=True)
def content_embed(monkeypatch):
    def fake(text):
        t = text.lower()
        return [1.0 if "alpha" in t else 0.0, 1.0 if "topic" in t else 0.0] + [0.0] * 382

    import artel.server.routes.memory as mem

    monkeypatch.setattr(mem, "embed", fake)
    return fake


async def _write(client, content):
    r = await client.post("/memory", json={"content": content}, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["id"]


async def _recall(client):
    return await client.get(
        "/memory/search",
        params={"q": "alpha topic", "limit": 5, "context": "recall"},
        headers=HEADERS,
    )


@pytest.mark.asyncio
async def test_recall_context_logs_surface_events(client, enable_bandit):
    from artel.store.db import get_db

    await _write(client, "alpha topic one")
    await _write(client, "alpha topic two")

    r = await _recall(client)
    assert r.status_code == 200
    surfaced = len(r.json())
    assert surfaced >= 1

    db = get_db()
    rows = db.execute("SELECT features, read_count_at FROM recall_events").fetchall()
    assert len(rows) == surfaced
    assert len(json.loads(rows[0]["features"])) == 6
    assert rows[0]["read_count_at"] == 1


@pytest.mark.asyncio
async def test_disabled_logs_nothing(client, monkeypatch):
    import artel.server.config as cfg

    monkeypatch.setattr(cfg.settings, "recall_bandit_enabled", False)
    from artel.store.db import get_db

    await _write(client, "alpha topic one")
    r = await _recall(client)
    assert r.status_code == 200
    assert get_db().execute("SELECT COUNT(*) FROM recall_events").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_reward_one_when_entry_reused_after_surfacing(client, enable_bandit):
    from artel.store import recall_bandit
    from artel.store.db import get_db

    eid = await _write(client, "alpha topic one")
    await _recall(client)  # surfaces eid, read_count -> 1, read_count_at recorded as 1

    db = get_db()
    with db:
        db.execute("UPDATE memory SET read_count = read_count + 1 WHERE id = ?", (eid,))

    future = datetime.now(UTC) + timedelta(hours=1)
    resolved = recall_bandit.resolve_rewards(db, future)
    assert resolved == 1

    ev = db.execute(
        "SELECT resolved, reward FROM recall_events WHERE entry_id = ?", (eid,)
    ).fetchone()
    assert ev["resolved"] == 1
    assert ev["reward"] == 1.0
    assert any(w != 0.0 for w in recall_bandit.load_state(db).weights)


@pytest.mark.asyncio
async def test_reward_zero_when_entry_not_reused(client, enable_bandit):
    from artel.store import recall_bandit
    from artel.store.db import get_db

    eid = await _write(client, "alpha topic one")
    await _recall(client)

    db = get_db()
    future = datetime.now(UTC) + timedelta(hours=1)
    assert recall_bandit.resolve_rewards(db, future) == 1
    ev = db.execute("SELECT reward FROM recall_events WHERE entry_id = ?", (eid,)).fetchone()
    assert ev["reward"] == 0.0


@pytest.mark.asyncio
async def test_grace_period_defers_unripe_events(client, enable_bandit):
    from artel.store import recall_bandit
    from artel.store.db import get_db

    await _write(client, "alpha topic one")
    await _recall(client)

    db = get_db()
    # now is right after surfacing — inside the grace window, nothing resolves yet
    assert recall_bandit.resolve_rewards(db, datetime.now(UTC), grace_seconds=1800) == 0
