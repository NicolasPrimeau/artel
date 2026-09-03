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
