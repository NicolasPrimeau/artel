"""Deterministic facts about the fleet.

Everything numeric answers from here, never from a model. An observability tool that
invents a spend figure is worse than no tool: the buyer is the person whose budget it
is, and a plausible wrong number is indistinguishable from a right one until it is
argued about in a meeting. The model is allowed to write prose over these numbers and
to cite them. It is never allowed to produce them.
"""

from __future__ import annotations

import collections
import datetime
import json
import pathlib
import sqlite3
import subprocess

TRANSCRIPTS = pathlib.Path.home() / ".claude" / "projects"
PROJECT_ROOT = pathlib.Path.home() / "projects"
DIR_PREFIXES = ("-home-nprimeau-projects-", "-home-nprimeau-")


def _proj_from_dir(name: str) -> str:
    for p in DIR_PREFIXES:
        if name.startswith(p):
            return name[len(p) :]
    return name


def token_usage(days: int = 7) -> dict[str, dict]:
    """Token volume per project, read from the transcripts on disk.

    Usage never reaches Artel today — the capture hook keeps the prose of a session
    and drops the usage block — so this reads the source rather than pretending the
    server already has it.
    """
    cut = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).timestamp()
    agg: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    if not TRANSCRIPTS.is_dir():
        return {}
    for d in TRANSCRIPTS.iterdir():
        if not d.is_dir():
            continue
        proj = _proj_from_dir(d.name)
        for f in d.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cut:
                    continue
            except OSError:
                continue
            agg[proj]["sessions"] += 1
            for line in f.open(errors="replace"):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                u = (rec.get("message") or {}).get("usage")
                if not u:
                    continue
                a = agg[proj]
                a["turns"] += 1
                a["input"] += u.get("input_tokens", 0)
                a["output"] += u.get("output_tokens", 0)
                a["cache_read"] += u.get("cache_read_input_tokens", 0)
                a["cache_write"] += u.get("cache_creation_input_tokens", 0)
    return {k: dict(v) for k, v in agg.items()}


def commits(days: int = 7) -> dict[str, dict]:
    out: dict[str, dict] = {}
    roots = [PROJECT_ROOT / d.name for d in PROJECT_ROOT.iterdir()] if PROJECT_ROOT.is_dir() else []
    roots.append(pathlib.Path.home() / "Steward")
    for repo in roots:
        if not (repo / ".git").is_dir():
            continue
        try:
            log = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    f"--since={days} days ago",
                    "--numstat",
                    "--pretty=format:%H",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except Exception:
            continue
        n = added = removed = 0
        for line in log.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                try:
                    added += int(parts[0])
                    removed += int(parts[1])
                except ValueError:
                    pass
            elif line.strip():
                n += 1
        out[repo.name] = {"commits": n, "added": added, "removed": removed}
    return out


def artel_counts(db_path: str, days: int = 7) -> dict[str, dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    out: dict[str, dict] = collections.defaultdict(dict)
    q = f"datetime('now','-{int(days)} days')"
    for r in db.execute(
        f"""SELECT COALESCE(project,'(unscoped)') p, COUNT(*) n FROM memory
            WHERE created_at > {q} AND deleted_at IS NULL AND agent_id != 'archivist'
            GROUP BY p"""
    ):
        out[r["p"]]["notes_by_agents"] = r["n"]
    for r in db.execute(
        f"""SELECT COALESCE(project,'(unscoped)') p, COUNT(*) n FROM memory
            WHERE created_at > {q} AND deleted_at IS NULL AND agent_id = 'archivist'
            GROUP BY p"""
    ):
        out[r["p"]]["notes_by_archivist"] = r["n"]
    for r in db.execute(
        "SELECT COALESCE(project,'(unscoped)') p, COUNT(*) n FROM decisions GROUP BY p"
    ):
        out[r["p"]]["decisions"] = r["n"]
    db.close()
    return {k: dict(v) for k, v in out.items()}


# Repo directory names and Artel project names are not the same string.
ALIAS = {
    "Nimbus": "nimbus",
    "formulai": "formulai",
    "Artel": "artel",
    "Yeet": "yeet",
    "Polaris": "polaris",
    "Longshot": "longshot",
    "nprimeau.dev": "blog",
    "Steward": "lighthouse",
}


def fleet_table(db_path: str, days: int = 7) -> list[dict]:
    tok, com, cnt = token_usage(days), commits(days), artel_counts(db_path, days)
    rows = []
    for proj, t in sorted(tok.items(), key=lambda kv: -kv[1].get("output", 0)):
        c = com.get(proj, {})
        a = cnt.get(ALIAS.get(proj, proj.lower()), {})
        n_commits = c.get("commits", 0)
        rows.append(
            {
                "project": proj,
                "sessions": t.get("sessions", 0),
                "turns": t.get("turns", 0),
                "output_tokens": t.get("output", 0),
                "cache_read": t.get("cache_read", 0),
                "commits": n_commits,
                "lines_added": c.get("added", 0),
                "tokens_per_commit": (t.get("output", 0) // n_commits) if n_commits else None,
                "notes": a.get("notes_by_agents", 0),
                "decisions": a.get("decisions", 0),
            }
        )
    return rows
