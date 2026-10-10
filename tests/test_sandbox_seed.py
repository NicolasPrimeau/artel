import importlib.util
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import artel.store.db as db_mod

SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "sandbox_seed.py"


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
