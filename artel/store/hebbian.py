import sqlite3
from datetime import UTC, datetime
from itertools import combinations

from . import decay

HALF_LIFE_DAYS = 14.0
RATE = 0.2
MIN_WEIGHT = 0.02


def _now() -> str:
    return datetime.now(UTC).isoformat()


def reinforce(db: sqlite3.Connection, ids: list[str], now: str | None = None) -> None:
    now = now or _now()
    for a, b in combinations(sorted(set(ids)), 2):
        row = db.execute(
            "SELECT weight, updated_at FROM hebbian_edge WHERE src=? AND dst=?", (a, b)
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO hebbian_edge (src, dst, weight, updated_at) VALUES (?,?,?,?)",
                (a, b, decay.reinforced(0.0, RATE), now),
            )
        else:
            w = decay.reinforced(
                decay.decayed(row["weight"], row["updated_at"], HALF_LIFE_DAYS, now), RATE
            )
            db.execute(
                "UPDATE hebbian_edge SET weight=?, updated_at=? WHERE src=? AND dst=?",
                (w, now, a, b),
            )


def neighbors(db: sqlite3.Connection, node_id: str, now: str | None = None) -> dict[str, float]:
    now = now or _now()
    out: dict[str, float] = {}
    for r in db.execute(
        "SELECT src, dst, weight, updated_at FROM hebbian_edge WHERE src=? OR dst=?",
        (node_id, node_id),
    ).fetchall():
        w = decay.decayed(r["weight"], r["updated_at"], HALF_LIFE_DAYS, now)
        if w < MIN_WEIGHT:
            continue
        other = r["dst"] if r["src"] == node_id else r["src"]
        out[other] = max(out.get(other, 0.0), w)
    return out


def prune(db: sqlite3.Connection, now: str | None = None) -> int:
    now = now or _now()
    doomed = [
        (r["src"], r["dst"])
        for r in db.execute("SELECT src, dst, weight, updated_at FROM hebbian_edge").fetchall()
        if decay.decayed(r["weight"], r["updated_at"], HALF_LIFE_DAYS, now) < MIN_WEIGHT
    ]
    for s, d in doomed:
        db.execute("DELETE FROM hebbian_edge WHERE src=? AND dst=?", (s, d))
    return len(doomed)
