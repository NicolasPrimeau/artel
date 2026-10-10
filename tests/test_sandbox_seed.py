import importlib.util
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import artel.store.db as db_mod

SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "sandbox_seed.py"
RESEED_DAYS = 7


@pytest.fixture
def seeded(tmp_path):
    spec = importlib.util.spec_from_file_location("sandbox_seed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db_mod._conn = None
    counts = mod.build(tmp_path / "seed.db", mod.load())
    yield counts, db_mod.get_db()
    db_mod._conn.close()
    db_mod._conn = None


def test_seed_populates_every_dashboard_view(seeded):
    counts, _ = seeded
    assert all(counts.values()), counts


def test_seeded_peers_are_never_polled(seeded):
    _, db = seeded
    soon = datetime.now(UTC) + timedelta(days=365)
    for row in db.execute("SELECT last_fetched_at, interval_min FROM feed_subscriptions"):
        last = datetime.fromisoformat(row["last_fetched_at"].replace("Z", "+00:00"))
        assert last + timedelta(minutes=row["interval_min"]) > soon


def test_seeded_run_is_in_flight(seeded):
    _, db = seeded
    statuses = {r["status"] for r in db.execute("SELECT status FROM tasks")}
    assert statuses == {"completed", "open"}


def test_specialities_name_only_seeded_projects(seeded):
    _, db = seeded
    projects = {r["project"] for r in db.execute("SELECT DISTINCT project FROM memory")}
    tags = {r["tag"] for r in db.execute("SELECT tag FROM task_affinity")}
    assert tags and tags <= projects


def test_pulse_outlives_the_reseed_interval(seeded):
    from artel.store import decay, hebbian

    _, db = seeded
    later = (datetime.now(UTC) + timedelta(days=RESEED_DAYS * 2)).isoformat()
    edges = db.execute("SELECT weight, updated_at FROM hebbian_edge").fetchall()
    alive = [
        e
        for e in edges
        if decay.decayed(e["weight"], e["updated_at"], hebbian.HALF_LIFE_DAYS, later)
        >= hebbian.MIN_WEIGHT
    ]
    assert len(alive) == len(edges)
