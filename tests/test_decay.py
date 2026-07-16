from datetime import UTC, datetime, timedelta

import pytest

from artel.store import decay


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_decayed_halves_at_half_life():
    assert decay.decayed(1.0, _iso(14), 14.0) == pytest.approx(0.5, rel=1e-3)
    assert decay.decayed(1.0, _iso(28), 14.0) == pytest.approx(0.25, rel=1e-3)


def test_decayed_no_decay_at_zero_age():
    assert decay.decayed(0.8, _iso(0), 14.0) == pytest.approx(0.8, rel=1e-3)


def test_decayed_zero_and_negative_weight():
    assert decay.decayed(0.0, _iso(1), 14.0) == 0.0
    assert decay.decayed(-1.0, _iso(1), 14.0) == 0.0


def test_decayed_unparseable_since_returns_weight():
    assert decay.decayed(0.7, None, 14.0) == 0.7
    assert decay.decayed(0.7, "not a timestamp", 14.0) == 0.7


def test_decayed_accepts_z_suffix_and_explicit_now():
    since = "2026-01-01T00:00:00.000Z"
    now = "2026-01-15T00:00:00.000Z"
    assert decay.decayed(1.0, since, 14.0, now) == pytest.approx(0.5, rel=1e-6)


def test_decayed_future_since_does_not_amplify():
    assert decay.decayed(1.0, _iso(-5), 14.0) == pytest.approx(1.0, rel=1e-6)


def test_reinforced_approaches_one_asymptotically():
    w = 0.0
    prev = w
    for _ in range(50):
        w = decay.reinforced(w, 0.2)
        assert prev < w <= 1.0
        prev = w
    assert w == pytest.approx(1.0, abs=1e-4)


def test_reinforced_from_zero_equals_rate():
    assert decay.reinforced(0.0, 0.3) == pytest.approx(0.3)
