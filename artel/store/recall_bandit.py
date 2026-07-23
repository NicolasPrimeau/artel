from __future__ import annotations

import json
import math
import secrets
from datetime import datetime

from . import bandit

BANDIT_DIM = 6
LEARNING_RATE = 0.1
GRACE_SECONDS = 1800

_WEIGHTS_KEY = "recall_bandit_weights"


def build_features(
    relevance: float,
    confidence: float,
    recency: float,
    trail: float,
    distinct_readers: int,
) -> list[float]:
    return [
        1.0,
        max(0.0, min(1.0, relevance)),
        max(0.0, min(1.0, confidence)),
        max(0.0, min(1.0, recency)),
        min(1.0, math.log1p(max(0.0, trail)) / math.log1p(10.0)),
        min(1.0, max(0, distinct_readers) / 5.0),
    ]


def load_state(db) -> bandit.BanditState:
    row = db.execute("SELECT value FROM kv WHERE key = ?", (_WEIGHTS_KEY,)).fetchone()
    if row and isinstance(row["value"], str):
        return bandit.loads(row["value"], BANDIT_DIM)
    return bandit.initial_state(BANDIT_DIM)


def save_state(db, state: bandit.BanditState) -> None:
    db.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_WEIGHTS_KEY, bandit.dumps(state)),
    )


def predict(db, features: list[float]) -> float:
    return bandit.predict(load_state(db), features)


def log_surface(
    db, agent_id: str, entry_id: str, features: list[float], read_count_at: int
) -> None:
    db.execute(
        "INSERT INTO recall_events (id, agent_id, entry_id, features, read_count_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (secrets.token_hex(16), agent_id, entry_id, json.dumps(features), read_count_at),
    )


def resolve_rewards(
    db,
    now: datetime,
    grace_seconds: int = GRACE_SECONDS,
    lr: float = LEARNING_RATE,
) -> int:
    rows = db.execute("SELECT * FROM recall_events WHERE resolved = 0").fetchall()
    state = load_state(db)
    resolved = 0
    for ev in rows:
        try:
            surfaced = datetime.fromisoformat(str(ev["surfaced_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - surfaced).total_seconds() < grace_seconds:
            continue
        row = db.execute("SELECT read_count FROM memory WHERE id = ?", (ev["entry_id"],)).fetchone()
        current = (row["read_count"] if row else 0) or 0
        reward = 1.0 if current > ev["read_count_at"] else 0.0
        try:
            features = json.loads(ev["features"])
        except (TypeError, ValueError):
            features = []
        if len(features) == BANDIT_DIM:
            state = bandit.update(state, features, reward, lr)
        db.execute(
            "UPDATE recall_events SET resolved = 1, reward = ? WHERE id = ?", (reward, ev["id"])
        )
        resolved += 1
    if resolved:
        save_state(db, state)
    return resolved
