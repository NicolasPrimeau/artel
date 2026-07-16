from datetime import UTC, datetime


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def decayed(
    weight: float, since: str | None, half_life_days: float, now: str | None = None
) -> float:
    if weight <= 0:
        return 0.0
    t = _parse(since)
    if t is None:
        return weight
    n = _parse(now) or datetime.now(UTC)
    age_days = max(0.0, (n - t).total_seconds() / 86400.0)
    return weight * 0.5 ** (age_days / half_life_days)


def reinforced(weight: float, rate: float) -> float:
    return weight + rate * (1.0 - weight)
