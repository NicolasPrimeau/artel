import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from artel.store import affinity
from artel.store.schema import SCHEMA

from .conftest import HEADERS, HEADERS2


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


# --- pure store (no app) ---------------------------------------------------------------


def test_reinforce_builds_per_tag_weights(db):
    affinity.reinforce(db, "ant", ["db", "infra"])
    s = affinity.scores(db, "ant")
    assert s["db"] == pytest.approx(affinity.RATE)
    assert s["infra"] == pytest.approx(affinity.RATE)


def test_reinforce_ignores_blank_tags_and_dedupes(db):
    affinity.reinforce(db, "ant", ["db", "db", "", "  "])
    assert list(affinity.scores(db, "ant")) == ["db"]


def test_scores_are_per_agent(db):
    affinity.reinforce(db, "ant", ["db"])
    assert affinity.scores(db, "bee") == {}


def test_affinity_decays_and_drops_below_threshold(db):
    affinity.reinforce(db, "ant", ["db"], now=_iso(affinity.HALF_LIFE_DAYS))
    s = affinity.scores(db, "ant")
    assert s["db"] == pytest.approx(affinity.RATE / 2, rel=1e-2)
    affinity.reinforce(db, "ant", ["ui"], now=_iso(affinity.HALF_LIFE_DAYS * 10))
    assert "ui" not in affinity.scores(db, "ant")


def test_repeat_completions_compound(db):
    affinity.reinforce(db, "ant", ["db"])
    once = affinity.scores(db, "ant")["db"]
    affinity.reinforce(db, "ant", ["db"])
    assert affinity.scores(db, "ant")["db"] > once


# --- endpoint (CI) ---------------------------------------------------------------------


async def _complete(client, title, tags):
    r = await client.post("/tasks", json={"title": title, "tags": tags}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)
    r = await client.post(f"/tasks/{tid}/complete", headers=HEADERS)
    assert r.status_code == 200
    return tid


async def test_completion_earns_affinity(client):
    import artel.store.db as db_mod

    await _complete(client, "index rebuild", ["db", "maintenance"])
    from tests.conftest import TEST_AGENT

    s = affinity.scores(db_mod.get_db(), TEST_AGENT)
    assert set(s) == {"db", "maintenance"}


async def test_recommended_ranks_by_earned_affinity(client):
    for _ in range(3):
        await _complete(client, "migration work", ["db"])
    await client.post("/tasks", json={"title": "style the header", "tags": ["ui"]}, headers=HEADERS)
    await client.post("/tasks", json={"title": "tune the index", "tags": ["db"]}, headers=HEADERS)
    r = await client.get("/tasks/recommended", headers=HEADERS)
    assert r.status_code == 200
    titles = [t["title"] for t in r.json()]
    assert titles[0] == "tune the index"
    assert "style the header" in titles


async def test_recommended_excludes_claimed_and_blocked(client):
    await _complete(client, "seed affinity", ["db"])
    open_task = await client.post(
        "/tasks", json={"title": "open db task", "tags": ["db"]}, headers=HEADERS
    )
    claimed = await client.post(
        "/tasks", json={"title": "claimed db task", "tags": ["db"]}, headers=HEADERS
    )
    await client.post(f"/tasks/{claimed.json()['id']}/claim", headers=HEADERS)
    blocker = await client.post("/tasks", json={"title": "blocker"}, headers=HEADERS)
    blocked = await client.post(
        "/tasks", json={"title": "blocked db task", "tags": ["db"]}, headers=HEADERS
    )
    await client.post(
        f"/tasks/{blocked.json()['id']}/dependencies",
        json={"depends_on": blocker.json()["id"]},
        headers=HEADERS,
    )
    r = await client.get("/tasks/recommended", headers=HEADERS)
    titles = [t["title"] for t in r.json()]
    assert "open db task" in titles
    assert "claimed db task" not in titles
    assert "blocked db task" not in titles
    assert open_task.status_code == 201


async def test_recommended_priority_breaks_affinity_ties(client):
    await client.post("/tasks", json={"title": "urgent thing", "priority": "high"}, headers=HEADERS)
    await client.post("/tasks", json={"title": "casual thing", "priority": "low"}, headers=HEADERS)
    r = await client.get("/tasks/recommended", headers=HEADERS)
    titles = [t["title"] for t in r.json()]
    assert titles.index("urgent thing") < titles.index("casual thing")


async def test_recommended_respects_project_membership(client):
    await client.post("/projects/secret/join", headers=HEADERS2)
    await client.post(
        "/tasks", json={"title": "secret task", "project": "secret"}, headers=HEADERS2
    )
    r = await client.get("/tasks/recommended", params={"project": "secret"}, headers=HEADERS)
    assert r.json() == []


async def test_affinity_is_personal_not_global(client):
    import artel.store.db as db_mod

    await _complete(client, "db grind", ["db"])
    from tests.conftest import AGENT2

    assert affinity.scores(db_mod.get_db(), AGENT2) == {}
