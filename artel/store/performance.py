import sqlite3
from datetime import UTC, datetime

OVERALL = ""
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
_MIN_SAMPLE = 5
_MIN_SUCCESS_RATE = 0.8


def _claim_seconds(db: sqlite3.Connection, task_id: str) -> float:
    row = db.execute(
        """SELECT created_at FROM task_comments
           WHERE task_id=? AND kind='claim' ORDER BY created_at DESC LIMIT 1""",
        (task_id,),
    ).fetchone()
    if not row:
        return 0.0
    try:
        claimed = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(UTC) - claimed).total_seconds())


def record(
    db: sqlite3.Connection, agent_id: str, task_id: str, tags: list[str], outcome: str
) -> None:
    seconds = _claim_seconds(db, task_id)
    completed = 1 if outcome == OUTCOME_COMPLETED else 0
    failed = 1 if outcome == OUTCOME_FAILED else 0
    for tag in [OVERALL, *dict.fromkeys(tags)]:
        db.execute(
            """INSERT INTO agent_performance (agent_id, tag, completed, failed, total_seconds)
               VALUES (?,?,?,?,?)
               ON CONFLICT(agent_id, tag) DO UPDATE SET
                 completed = completed + excluded.completed,
                 failed = failed + excluded.failed,
                 total_seconds = total_seconds + excluded.total_seconds,
                 updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (agent_id, tag, completed, failed, seconds),
        )


def summary(db: sqlite3.Connection, tags: list[str] | None = None) -> list[dict]:
    sql = "SELECT * FROM agent_performance"
    params: list = []
    if tags:
        placeholders = ",".join("?" * len(tags))
        sql += f" WHERE tag IN ({placeholders})"
        params.extend(tags)
    sql += " ORDER BY completed DESC"
    out = []
    for r in db.execute(sql, params).fetchall():
        attempts = r["completed"] + r["failed"]
        out.append(
            {
                "agent_id": r["agent_id"],
                "tag": r["tag"],
                "completed": r["completed"],
                "failed": r["failed"],
                "attempts": attempts,
                "success_rate": round(r["completed"] / attempts, 3) if attempts else 0.0,
                "avg_seconds": round(r["total_seconds"] / attempts, 1) if attempts else 0.0,
                "updated_at": r["updated_at"],
            }
        )
    return out


def best_for(db: sqlite3.Connection, tags: list[str], eligible: set[str]) -> tuple[str, str] | None:
    """Pick an agent from recorded history when the record is strong enough to beat a guess.

    Returns None unless someone has a real track record on one of these tags —
    a thin record is worse than the LLM's judgement, not better.
    """
    if not tags or not eligible:
        return None
    best: tuple[str, str] | None = None
    best_key = (_MIN_SAMPLE - 1, _MIN_SUCCESS_RATE)
    for row in summary(db, tags):
        if row["agent_id"] not in eligible:
            continue
        if row["attempts"] < _MIN_SAMPLE or row["success_rate"] < _MIN_SUCCESS_RATE:
            continue
        key = (row["attempts"], row["success_rate"])
        if (key[1], key[0]) > (best_key[1], best_key[0]):
            best_key = key
            best = (
                row["agent_id"],
                f"{row['completed']}/{row['attempts']} completed on '{row['tag']}'"
                f" ({int(row['success_rate'] * 100)}% success)",
            )
    return best
