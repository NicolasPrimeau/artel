import argparse
import json
import pathlib
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SEED = pathlib.Path(__file__).with_name("sandbox_seed.json")


def load(path: pathlib.Path = SEED) -> dict:
    return json.loads(path.read_text())


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build(path: pathlib.Path, seed: dict, embed: bool = False) -> dict:
    from artel.store.db import get_db

    if path.exists():
        path.unlink()
    db = get_db(str(path))
    latest = max(_parse(u["created_at"]) for u in seed["usage_events"])
    offset = datetime.now(UTC) - latest - timedelta(hours=1)

    def at(ts: str | None) -> str | None:
        return _fmt(_parse(ts) + offset) if ts else None

    for agent in seed["agents"]:
        db.execute(
            "INSERT OR IGNORE INTO agents (id, api_key, role) VALUES (?,?,?)",
            (agent, secrets.token_urlsafe(32), "archivist" if agent == "archivist" else "agent"),
        )
    for u in seed["usage_events"]:
        db.execute(
            """INSERT INTO usage_events
               (id, agent_id, project, session_id, model, billing_mode, turns,
                input_tokens, output_tokens, cache_read, cache_write,
                window_start, window_end, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                u["agent_id"],
                u["project"],
                u["session_id"],
                u["model"],
                u["billing_mode"],
                u["turns"],
                u["input_tokens"],
                u["output_tokens"],
                u["cache_read"],
                u["cache_write"],
                at(u["window_start"]),
                at(u["window_end"]),
                at(u["created_at"]),
            ),
        )
    for d in seed["decisions"]:
        db.execute(
            """INSERT INTO decisions
               (id, project, agent_id, decision, rationale, alternatives, created_at, session_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                d["project"],
                d["agent_id"],
                d["decision"],
                d["rationale"],
                json.dumps(d["alternatives"]),
                at(d["created_at"]),
                d["session_id"],
            ),
        )
    ids = []
    for m in seed["memory"]:
        mid = str(uuid.uuid4())
        ids.append((mid, m["content"]))
        db.execute(
            """INSERT INTO memory
               (id, type, agent_id, project, scope, content, confidence, tags,
                read_count, headline, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                m["type"],
                m["agent_id"],
                m["project"],
                "project",
                m["content"],
                m["confidence"],
                json.dumps(m["tags"]),
                m["read_count"],
                m["headline"],
                at(m["created_at"]),
                at(m["updated_at"]),
            ),
        )
        db.execute("INSERT INTO memory_fts (id, content) VALUES (?, ?)", (mid, m["content"]))
    if embed:
        from artel.store.embeddings import embed as vector

        for mid, content in ids:
            vec = vector(content)
            if vec is None:
                raise RuntimeError("embedding model unavailable; refusing to seed without vectors")
            db.execute(
                "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (mid, json.dumps(vec))
            )
    db.commit()
    return {
        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("usage_events", "decisions", "memory", "agents")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args()
    counts = build(pathlib.Path(args.out), load(), embed=not args.no_embed)
    print(f"seeded {args.out}")
    for t, n in counts.items():
        print(f"  {n:>5}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
