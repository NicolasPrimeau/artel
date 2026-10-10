import argparse
import asyncio
import json
import pathlib
import random
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SEED = pathlib.Path(__file__).with_name("sandbox_seed.json")
PROCEDURES = pathlib.Path(__file__).with_name("sandbox_procedures.json")
PEERS = (("https://artel.workshop.example", None), ("https://artel.laptop.example", "artel"))
SESSION_TAG = "session:"
CONFLICT_TAG = "sync-conflict"
NEVER_POLL_MIN = 5_256_000


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
    notes = []
    for m in seed["memory"]:
        mid = str(uuid.uuid4())
        ids.append((mid, m["content"]))
        notes.append((mid, m))
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
    _pulse(db, seed, notes, at)
    _mesh(db, seed)
    db.commit()
    asyncio.run(_procedures(json.loads(PROCEDURES.read_text())))
    return {
        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in (
            "usage_events",
            "decisions",
            "memory",
            "agents",
            "memory_edge",
            "hebbian_edge",
            "task_affinity",
            "peer_links",
            "blueprints",
            "blueprint_runs",
        )
    }


def _pulse(db, seed: dict, notes: list, at) -> None:
    rng = random.Random(0)
    now = datetime.now(UTC)

    def ago(hours: float) -> str:
        return _fmt(now - timedelta(hours=hours))

    groups: dict[tuple, list[str]] = {}
    for mid, m in notes:
        for tag in m["tags"]:
            if tag.startswith(SESSION_TAG):
                groups.setdefault((m["project"], tag), []).append(mid)
    for (project, _), members in sorted(groups.items(), key=lambda g: (str(g[0][0]), g[0][1])):
        for i in range(1, len(members)):
            if rng.random() < 0.55:
                db.execute(
                    "INSERT OR IGNORE INTO memory_edge (id, project, src, dst, rel, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        project,
                        members[i],
                        members[rng.randrange(max(0, i - 4), i)],
                        "contradicts" if rng.random() < 0.03 else "corroborates",
                        ago(rng.uniform(1, 240)),
                    ),
                )

    read = sorted((n for n in notes if n[1]["read_count"]), key=lambda n: -n[1]["read_count"])
    for mid, m in read:
        db.execute(
            "UPDATE memory SET trail=?, trail_at=?, last_read_at=? WHERE id=?",
            (float(m["read_count"]), ago(rng.uniform(1, 48)), ago(rng.uniform(1, 48)), mid),
        )
    for i, (mid, m) in enumerate(read):
        mates = [o for o, om in read[:i] if om["project"] == m["project"]]
        for other in rng.sample(mates, min(1, len(mates))):
            db.execute(
                "INSERT OR IGNORE INTO hebbian_edge (src, dst, weight, updated_at) VALUES (?,?,?,?)",
                (*sorted((mid, other)), round(rng.uniform(0.25, 0.95), 3), ago(rng.uniform(1, 72))),
            )

    turns: dict[str, dict[str, int]] = {}
    seen: dict[str, str] = {}
    for u in seed["usage_events"]:
        if u["project"]:
            by = turns.setdefault(u["agent_id"], {})
            by[u["project"]] = by.get(u["project"], 0) + u["turns"]
        seen[u["agent_id"]] = max(seen.get(u["agent_id"], ""), at(u["created_at"]))
    for agent, by in turns.items():
        top = max(by.values())
        for project, n in by.items():
            db.execute(
                "INSERT OR REPLACE INTO task_affinity (agent_id, tag, weight, updated_at)"
                " VALUES (?,?,?,?)",
                (agent, project, round(0.3 + 0.65 * n / top, 3), ago(rng.uniform(1, 72))),
            )
    for mid, m in notes:
        seen[m["agent_id"]] = max(seen.get(m["agent_id"], ""), at(m["updated_at"]))
    for agent, ts in seen.items():
        db.execute("UPDATE agents SET last_seen_at=? WHERE id=?", (ts, agent))
    members = {(m["project"], m["agent_id"]) for _, m in notes if m["project"]}
    members |= {(project, agent) for agent, by in turns.items() for project in by}
    for project, agent in sorted(members):
        db.execute(
            "INSERT OR IGNORE INTO project_members (project_id, agent_id) VALUES (?,?)",
            (project, agent),
        )

    mid, m = next(n for n in notes if n[1]["project"] == PEERS[1][1])
    db.execute(
        """INSERT INTO memory
           (id, type, agent_id, project, scope, content, confidence, tags, parents,
            headline, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            m["type"],
            m["agent_id"],
            m["project"],
            "project",
            m["content"],
            m["confidence"],
            json.dumps([*m["tags"], CONFLICT_TAG]),
            json.dumps([mid]),
            m["headline"],
            ago(6),
            ago(6),
        ),
    )


async def _procedures(spec: dict) -> None:
    from artel.server.blueprint import BlueprintCreate, BlueprintInstantiate
    from artel.server.models import TaskAction
    from artel.server.routes.blueprints import create_blueprint, instantiate_blueprint
    from artel.server.routes.tasks import claim_task, complete_task

    agent, project = spec["agent"], spec["project"]
    for document in spec["blueprints"]:
        await create_blueprint(BlueprintCreate(document=document, project=project), agent)
    run = await instantiate_blueprint(
        spec["run"]["name"],
        BlueprintInstantiate(params=spec["run"]["params"], project=project),
        agent,
    )
    for node in run.nodes[: spec["run"]["complete"]]:
        await claim_task(node.task_id, TaskAction(), agent)
        await complete_task(node.task_id, TaskAction(), agent)


def _mesh(db, seed: dict) -> None:
    owner = next(a for a in seed["agents"] if a != "archivist")
    now = _fmt(datetime.now(UTC))
    for url, project in PEERS:
        feed_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO feed_subscriptions
               (id, agent_id, project, url, name, tags, interval_min, max_per_poll, last_fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                feed_id,
                owner,
                project,
                f"{url}/memory/feed.json",
                f"mesh:{url}",
                json.dumps(["mesh", "peer"]),
                NEVER_POLL_MIN,
                100,
                now,
            ),
        )
        db.execute(
            "INSERT INTO peer_links (id, peer_url, project, feed_id, created_by) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), url, project, feed_id, owner),
        )
    db.execute(
        "INSERT INTO mesh_tokens (id, token, label, project, created_by) VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), secrets.token_urlsafe(32), "laptop", PEERS[1][1], owner),
    )


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
