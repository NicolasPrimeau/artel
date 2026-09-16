"""Figures for the ledger, read straight from the store.

Everything here is arithmetic over usage_events, decisions and memory. Nothing calls
a model: the numbers a budget owner argues about are computed and inspectable, and a
generated figure would be indistinguishable from a real one until it was wrong.

Deliberately no git or transcript access. Running inside the Artel container there is
neither, and needing them would mean the ledger only worked on the machine that
happened to hold the repos — which is not a product.
"""

from __future__ import annotations

import json
import re

from ..store import pricing
from ..store.db import get_db


def _window(days: int) -> str:
    return f"COALESCE(window_end, created_at) > datetime('now','-{int(days)} days')"


def by_project(days: int = 7) -> list[dict]:
    db = get_db()
    out: dict[str, dict] = {}
    for r in db.execute(
        f"""SELECT COALESCE(project,'(unscoped)') p, model, billing_mode,
                   COUNT(DISTINCT session_id) sessions, SUM(turns) turns,
                   SUM(input_tokens) i, SUM(output_tokens) o,
                   SUM(cache_read) cr, SUM(cache_write) cw
            FROM usage_events WHERE {_window(days)}
            GROUP BY p, model, billing_mode"""
    ):
        row = out.setdefault(
            r["p"],
            {
                "project": r["p"],
                "sessions": 0,
                "turns": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "billed": 0.0,
                "equivalent": 0.0,
                "unpriced": [],
            },
        )
        usage = {
            "input_tokens": r["i"],
            "output_tokens": r["o"],
            "cache_read": r["cr"],
            "cache_write": r["cw"],
        }
        row["sessions"] += r["sessions"]
        row["turns"] += r["turns"]
        row["output_tokens"] += r["o"] or 0
        row["cache_read"] += r["cr"] or 0
        c = pricing.cost_usd(r["model"], r["billing_mode"], usage)
        if c["amount"] is None:
            row["unpriced"].append(r["model"])
        elif c.get("billed"):
            row["billed"] += c["amount"]
        else:
            row["equivalent"] += c["amount"]

    counts = {
        r["p"]: r["n"]
        for r in db.execute(
            f"""SELECT COALESCE(project,'(unscoped)') p, COUNT(*) n FROM decisions
                WHERE created_at > datetime('now','-{int(days)} days') GROUP BY p"""
        )
    }
    for p, row in out.items():
        row["decisions"] = counts.get(p, 0)
        # Six places, not two. Rounding to cents in the data layer silently zeroes
        # anything under half a cent — which is most single calls on a cheap model.
        # Display rounds; storage does not.
        row["billed"] = round(row["billed"], 6)
        row["equivalent"] = round(row["equivalent"], 6)
        row["unpriced"] = sorted(set(row["unpriced"]))
    return sorted(out.values(), key=lambda r: -r["output_tokens"])


def by_session(days: int = 7, limit: int = 40) -> list[dict]:
    db = get_db()
    rows = []
    for r in db.execute(
        f"""SELECT session_id, COALESCE(project,'(unscoped)') project, model, billing_mode,
                   SUM(turns) turns, SUM(input_tokens) i, SUM(output_tokens) o,
                   SUM(cache_read) cr, SUM(cache_write) cw,
                   MIN(window_start) started, MAX(window_end) ended
            FROM usage_events WHERE {_window(days)}
            GROUP BY session_id, project, model, billing_mode
            ORDER BY o DESC LIMIT ?""",
        (limit,),
    ):
        usage = {
            "input_tokens": r["i"],
            "output_tokens": r["o"],
            "cache_read": r["cr"],
            "cache_write": r["cw"],
        }
        rows.append(
            {
                "session_id": r["session_id"],
                "project": r["project"],
                "model": r["model"],
                "turns": r["turns"],
                "output_tokens": r["o"],
                "cache_read": r["cr"],
                "started": r["started"],
                "ended": r["ended"],
                "cost": pricing.cost_usd(r["model"], r["billing_mode"], usage),
            }
        )
    return rows


def by_decision(days: int = 30, limit: int = 30) -> list[dict]:
    """Cost attributed to a decision through the session that produced it.

    Attribution by shared session, not causation. A session that produced three
    decisions spent its tokens on all three and on everything else it did, so this is
    the cost of the work containing the choice. Not divided between decisions —
    dividing would invent a precision the data has not got.
    """
    db = get_db()
    out = []
    for d in db.execute(
        f"""SELECT id, decision, rationale, alternatives, project, session_id, created_at
            FROM decisions
            WHERE created_at > datetime('now','-{int(days)} days')
            ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ):
        amount, priced, tokens = 0.0, False, 0
        if d["session_id"]:
            for u in db.execute(
                """SELECT model, billing_mode, SUM(input_tokens) i, SUM(output_tokens) o,
                          SUM(cache_read) cr, SUM(cache_write) cw
                   FROM usage_events WHERE session_id=? GROUP BY model, billing_mode""",
                (d["session_id"],),
            ):
                usage = {
                    "input_tokens": u["i"] or 0,
                    "output_tokens": u["o"] or 0,
                    "cache_read": u["cr"] or 0,
                    "cache_write": u["cw"] or 0,
                }
                tokens += usage["output_tokens"]
                c = pricing.cost_usd(u["model"], u["billing_mode"], usage)
                if c["amount"] is not None:
                    amount += c["amount"]
                    priced = True
        try:
            alts = json.loads(d["alternatives"] or "[]")
        except ValueError:
            alts = []
        out.append(
            {
                "id": d["id"],
                "decision": d["decision"],
                "rationale": d["rationale"],
                "alternatives": alts,
                "project": d["project"],
                "session_id": d["session_id"],
                "created_at": d["created_at"],
                "session_output_tokens": tokens,
                "session_cost": round(amount, 6) if priced else None,
            }
        )
    return out


def totals(days: int = 7) -> dict:
    rows = by_project(days)
    return {
        "billed": round(sum(r["billed"] for r in rows), 6),
        "equivalent": round(sum(r["equivalent"] for r in rows), 6),
        "unpriced": sorted({m for r in rows for m in r["unpriced"]}),
        "sessions": sum(r["sessions"] for r in rows),
        "decisions": sum(r["decisions"] for r in rows),
    }


# Sentences the fleet wrote about work it repeated or did by hand. Deterministic on
# purpose: a model asked "what should we automate?" will produce plausible suggestions
# whether or not the evidence exists, and an unfalsifiable answer is worth nothing to
# someone deciding where to spend a week. Every candidate here quotes the note it came
# from and links to it.
_REDO = r"(?:run|apply|applied|generate[d]?|create[d]?|sync(?:ed)?|deploy(?:ed)?|build|built|fresh(?:ed)?|do|done|import(?:ed)?)"

# Strong triggers name the labour directly. Weak ones only describe an invariant
# ("keep X in sync") that an automated job satisfies just as often as a human does,
# so they count only when the same sentence also names the labour.
_STRONG = (
    r"manually|by hand|forget to|"
    rf"(?:have to|need to|must be|has to) re-?{_REDO}|"
    rf"re-?{_REDO} (?:it|them|this|each|every|by hand|manually)"
)
_WEAK = r"keeps? in sync|kept in sync|must be kept|every time|each time"
# A sentence ends at ". ", not at any dot: `marketing/plans.html` and
# `refresh_rates.py` were cutting the evidence mid-filename, so a quote could
# begin "html` must be manually kept in sync".
_SENT = r"(?:[^.\n]|\.(?!\s|$))*"
_TOIL = re.compile(rf"{_SENT}\b({_STRONG}|{_WEAK})\b{_SENT}", re.I)
_STRONG_RE = re.compile(rf"\b({_STRONG})\b", re.I)

# A sentence can name manual work and still be evidence that nobody does it by hand:
# "do not manually edit coverage" is an instruction not to. Only a negation attached
# to the trigger itself disqualifies. An earlier version also disqualified any mention
# of automation, which threw away the best evidence in the corpus -- "migrations must
# be applied manually" and "will stay stale until this is done manually" both name
# automation in the same breath as the labour it fails to cover.
_ANTI = re.compile(
    r"(?:do(?:es)? not|don'?t|never|no longer|without|cannot|can'?t|avoid)\s+"
    r"(?:\w+\s+){0,2}(?:manual|by hand)"
    r"|(?:instead of|rather than)\s+(?:\w+\s+){0,3}(?:manual|by hand)"
    r"|no (?:need|longer needs?) to\s+(?:\w+\s+){0,2}(?:manual|by hand)"
    # Doing something by hand on purpose is a strategy, not a backlog item:
    # "sell manually first, automate after first customer" is the playbook working.
    r"|\bsell\w*\s+(?:\w+\s+){0,2}(?:manual|by hand)"
    r"|\b(?:playbook|thesis|arbitrage)\b"
    # Someone else's labour. A subscriber reconstructing a map by hand is a UX
    # finding; it is not work this fleet can automate away.
    r"|\b(?:users?|subscribers?|customers?|contractors?|vendors?)\b[^.]{0,40}(?:manual|by hand)",
    re.I,
)


def _is_toil(snippet: str) -> bool:
    if not _STRONG_RE.search(snippet):
        return False
    return not _ANTI.search(snippet)


# What the sentence is about, so twelve one-off quotes become a handful of themes.
# Order is precedence: the first pattern that matches wins, so the most specific
# signal goes first. "kept in sync" is a stronger clue than the word "billing"
# appearing inside a path like `billing/plans.py`.
_THEMES = (
    (
        "cross-copy in sync",
        r"\b(in sync|sync\w*|cop(?:y|ied|ies)|mirror\w*|duplicat\w+|splice\w*)\b",
    ),
    ("deploy / migrate", r"\b(deploy\w*|migrat\w+|alembic|upgrade head|release\w*|rollout)\b"),
    (
        "data refresh",
        r"\b(refresh\w*|backfill\w*|reprocess\w*|regenerat\w+|rebuild\w*|ingest\w*)\b",
    ),
    ("monitoring / alerts", r"\b(alert\w*|alarm\w*|baseline|monitor\w*|drift)\b"),
    ("credentials / login", r"\b(2fa|login|oauth|sso|token\w*|verification|consent|api key)\b"),
    ("billing / accounts", r"\b(stripe|subscription\w*|trial|invoice\w*|reactivat\w+|refund\w*)\b"),
    (
        "provisioning",
        r"\b(share\w*|grant\w*|schedul\w+|eventbridge|endpoint\w*|queue|sqs|whitelist)\b",
    ),
    ("content / i18n", r"\b(translation\w*|translat\w+|wording|sitemap|markup|nav|email\w*)\b"),
    (
        "scripts / tooling",
        r"\b(script\w*|command\w*|cli|makefile|hook\w*|pipeline\w*|workflow\w*)\b",
    ),
    ("ops / restart", r"\b(restart\w*|crash\w*|reboot\w*|failover|recover\w*)\b"),
)


def toil(days: int = 90, project: str | None = None, limit: int = 25) -> list[dict]:
    """Repeated or hand-run work, mined from what the fleet wrote about itself."""
    db = get_db()
    sql = (
        "SELECT id, project, content, created_at FROM memory "
        f"WHERE deleted_at IS NULL AND created_at > datetime('now','-{int(days)} days')"
    )
    args: list = []
    if project:
        sql += " AND project = ?"
        args.append(project)
    out: list[dict] = []
    seen: set[str] = set()
    for r in db.execute(sql, args):
        for m in _TOIL.finditer(r["content"] or ""):
            snippet = " ".join(m.group(0).split())
            if len(snippet) < 40:
                continue
            if not _is_toil(snippet):
                continue
            key = snippet.lower()[:60]
            if key in seen:
                continue
            seen.add(key)
            theme = "other"
            for name, pat in _THEMES:
                if re.search(pat, snippet, re.I):
                    theme = name
                    break
            out.append(
                {
                    "entry_id": r["id"],
                    "project": r["project"],
                    "theme": theme,
                    "evidence": snippet[:220],
                    "created_at": r["created_at"],
                }
            )
    counts: dict[str, int] = {}
    for c in out:
        counts[c["theme"]] = counts.get(c["theme"], 0) + 1
    # "other" last: an unclassified bucket at the top buries the themes that are
    # actually actionable.
    out.sort(key=lambda c: (c["theme"] == "other", -counts[c["theme"]], c["theme"]))
    return out[:limit]


def toil_themes(days: int = 90, project: str | None = None) -> list[dict]:
    rows = toil(days, project, limit=500)
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["theme"], {"theme": r["theme"], "count": 0, "projects": set()})
        a["count"] += 1
        if r["project"]:
            a["projects"].add(r["project"])
    return sorted(
        ({**a, "projects": sorted(a["projects"])} for a in agg.values()),
        key=lambda a: (a["theme"] == "other", -a["count"]),
    )
