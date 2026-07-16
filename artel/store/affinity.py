import sqlite3
from datetime import UTC, datetime

from . import decay

HALF_LIFE_DAYS = 30.0
RATE = 0.3
MIN_WEIGHT = 0.05


def _now() -> str:
    return datetime.now(UTC).isoformat()


def reinforce(
    db: sqlite3.Connection, agent_id: str, tags: list[str], now: str | None = None
) -> None:
    now = now or _now()
    for tag in {t.strip() for t in tags if t and t.strip()}:
        row = db.execute(
            "SELECT weight, updated_at FROM task_affinity WHERE agent_id=? AND tag=?",
            (agent_id, tag),
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO task_affinity (agent_id, tag, weight, updated_at) VALUES (?,?,?,?)",
                (agent_id, tag, decay.reinforced(0.0, RATE), now),
            )
        else:
            w = decay.reinforced(
                decay.decayed(row["weight"], row["updated_at"], HALF_LIFE_DAYS, now), RATE
            )
            db.execute(
                "UPDATE task_affinity SET weight=?, updated_at=? WHERE agent_id=? AND tag=?",
                (w, now, agent_id, tag),
            )


def scores(db: sqlite3.Connection, agent_id: str, now: str | None = None) -> dict[str, float]:
    now = now or _now()
    out: dict[str, float] = {}
    for r in db.execute(
        "SELECT tag, weight, updated_at FROM task_affinity WHERE agent_id=?", (agent_id,)
    ).fetchall():
        w = decay.decayed(r["weight"], r["updated_at"], HALF_LIFE_DAYS, now)
        if w >= MIN_WEIGHT:
            out[r["tag"]] = w
    return out
