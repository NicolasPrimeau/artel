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


def token_usage_from_ledger(db_path: str, days: int = 7) -> dict[str, dict]:
    """Artel's own usage ledger — the authoritative source once the drainer is shipping.

    Preferred over reading transcripts: the ledger is what a customer's server holds,
    so the demo exercises the real path rather than a local shortcut only this machine
    can take.
    """
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    out: dict[str, dict] = {}
    try:
        rows = db.execute(
            f"""SELECT COALESCE(project,'(unscoped)') p, SUM(turns) turns,
                       SUM(input_tokens) input, SUM(output_tokens) output,
                       SUM(cache_read) cache_read, SUM(cache_write) cache_write,
                       COUNT(DISTINCT session_id) sessions
                FROM usage_events
                WHERE COALESCE(window_end, created_at) > datetime('now','-{int(days)} days')
                GROUP BY p"""
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        db.close()
    for r in rows:
        out[r["p"]] = {
            k: r[k] for k in ("turns", "input", "output", "cache_read", "cache_write", "sessions")
        }
    return out


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
def decisions(db_path: str, limit: int = 40, project: str | None = None) -> list[dict]:
    """The record of what was chosen — mined by the archivist from captures, not filed
    by agents, because instructions are advisory and this has to be there whether or
    not anyone remembered."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    sql = "SELECT * FROM decisions"
    args: list = []
    if project:
        sql += " WHERE project = ?"
        args.append(project)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    try:
        rows = db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    out = []
    for r in rows:
        try:
            alts = json.loads(r["alternatives"] or "[]")
        except Exception:
            alts = []
        out.append(
            {
                "id": r["id"],
                "project": r["project"],
                "agent": r["agent_id"],
                "decision": r["decision"],
                "rationale": r["rationale"],
                "alternatives": alts,
                "created_at": r["created_at"],
            }
        )
    return out


def usage_by_model(db_path: str, days: int = 7, project: str | None = None) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    where = [f"COALESCE(window_end, created_at) > datetime('now','-{int(days)} days')"]
    args: list = []
    if project:
        where.append("project = ?")
        args.append(project)
    try:
        rows = db.execute(
            f"""SELECT COALESCE(project,'(unscoped)') project, model, billing_mode,
                       SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,
                       SUM(cache_read) cache_read, SUM(cache_write) cache_write,
                       SUM(turns) turns
                FROM usage_events WHERE {" AND ".join(where)}
                GROUP BY project, model, billing_mode ORDER BY output_tokens DESC""",
            args,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    return [dict(r) for r in rows]


def cost_for(model_rows: list[dict]) -> dict:
    """List-price value of usage rows, via Artel's own pricing module."""
    import sys as _sys

    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from artel.store import pricing

    billed = equivalent = 0.0
    unpriced: list[str] = []
    for r in model_rows:
        c = pricing.cost_usd(r["model"], r.get("billing_mode", "unknown"), r)
        if c["amount"] is None:
            unpriced.append(r["model"])
        elif c.get("billed"):
            billed += c["amount"]
        else:
            equivalent += c["amount"]
    return {
        "billed": round(billed, 2),
        "equivalent": round(equivalent, 2),
        "unpriced": sorted(set(unpriced)),
    }


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
    # Ledger first; fall back to transcripts only where it has nothing yet, so the
    # table is never silently empty on a fleet that has not shipped usage.
    ledger = token_usage_from_ledger(db_path, days)
    tok = ledger or token_usage(days)
    source = "artel ledger" if ledger else "local transcripts"
    com, cnt = commits(days), artel_counts(db_path, days)
    rows = []
    # The ledger keys by Artel project ("nimbus"); commits key by repo directory
    # ("Nimbus"). Joining on the raw string silently drops every row whose two names
    # differ in case — which is all of them except the one that happens to match.
    to_repo = {v: k for k, v in ALIAS.items()}
    for proj, t in sorted(tok.items(), key=lambda kv: -kv[1].get("output", 0)):
        repo = to_repo.get(proj, proj)
        c = com.get(repo, com.get(proj, {}))
        a = cnt.get(ALIAS.get(proj, proj), cnt.get(proj, {}))
        n_commits = c.get("commits", 0)
        rows.append(
            {
                "source": source,
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
