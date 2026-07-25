import pytest

from tests.conftest import HEADERS, HEADERS2


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


async def _recall(client, headers=HEADERS):
    r = await client.get("/memory/search", params={"q": "alpha topic", "limit": 5}, headers=headers)
    assert r.status_code == 200
    return r.json()


def _set_confidence(eid, value):
    from artel.store.db import get_db

    db = get_db()
    with db:
        db.execute("UPDATE memory SET confidence = ? WHERE id = ?", (value, eid))


def _confidence(eid):
    from artel.store.db import get_db

    return get_db().execute("SELECT confidence FROM memory WHERE id = ?", (eid,)).fetchone()[0]


@pytest.mark.asyncio
async def test_search_reinforces_low_confidence_entry(client):
    import artel.server.config as cfg

    eid = await _write(client, "alpha topic decayed but useful")
    _set_confidence(eid, 0.05)

    hits = await _recall(client, headers=HEADERS2)
    assert any(e["id"] == eid for e in hits)
    expected = 0.05 + cfg.settings.recall_reinforce_gain * (1.0 - 0.05)
    assert _confidence(eid) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_repeated_recall_climbs_above_decay_threshold(client):
    eid = await _write(client, "alpha topic reused often")
    _set_confidence(eid, 0.05)

    for _ in range(12):
        await _recall(client, headers=HEADERS2)

    assert _confidence(eid) > 0.7


@pytest.mark.asyncio
async def test_reinforcement_caps_at_one(client):
    eid = await _write(client, "alpha topic already trusted")
    _set_confidence(eid, 0.99)

    for _ in range(20):
        await _recall(client, headers=HEADERS2)

    assert _confidence(eid) <= 1.0


@pytest.mark.asyncio
async def test_archivist_search_does_not_reinforce(client, monkeypatch):
    import artel.server.config as cfg

    monkeypatch.setattr(cfg.settings, "archivist_agent_id", "testagent")

    eid = await _write(client, "alpha topic scanned by archivist")
    _set_confidence(eid, 0.05)

    await _recall(client, headers=HEADERS)  # testagent is now the archivist
    assert _confidence(eid) == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_get_by_id_does_not_reinforce(client):
    eid = await _write(client, "alpha topic direct fetch")
    _set_confidence(eid, 0.05)

    r = await client.get(f"/memory/{eid}", headers=HEADERS2)
    assert r.status_code == 200
    assert _confidence(eid) == pytest.approx(0.05)
