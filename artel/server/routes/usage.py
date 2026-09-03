import json

from fastapi import APIRouter, Query

from ...store import pricing
from ...store.db import get_db, norm_project
from ..auth import ActorDep, ReaderDep, default_project_for, project_filter
from ..models import UsageRollup, new_id

router = APIRouter(prefix="/usage", tags=["usage"])


@router.post("", status_code=201, summary="Record a token-usage rollup for a session")
async def write_usage(body: UsageRollup, agent_id: str = ActorDep):
    project = norm_project(body.project) or default_project_for(agent_id)
    db = get_db()
    with db:
        # INSERT OR IGNORE against idx_usage_dedup: a drainer that re-reads a
        # transcript must not bill the same window twice.
        db.execute(
            """INSERT OR IGNORE INTO usage_events
               (id, agent_id, project, session_id, model, billing_mode, turns,
                input_tokens, output_tokens, cache_read, cache_write,
                window_start, window_end)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id(),
                agent_id,
                project,
                body.session_id,
                body.model,
                body.billing_mode,
                body.turns,
                body.input_tokens,
                body.output_tokens,
                body.cache_read,
                body.cache_write,
                body.window_start,
                body.window_end,
            ),
        )
    return {"status": "recorded", "project": project}


@router.get("", summary="Token usage and cost, grouped by project and model")
async def read_usage(
    agent_id: str = ReaderDep,
    days: int = Query(default=7, ge=1, le=365),
    project: str | None = None,
):
    db = get_db()
    where, params = project_filter(agent_id)
    # Filter on when the tokens were SPENT, not when the row was written. A backfill
    # writes months of history in one pass; keyed on created_at it would all appear as
    # today's spend, which is a plausible wrong number of exactly the kind this
    # endpoint exists to avoid. window_end is absent on rollups from transcripts with
    # no timestamps, so fall back to created_at there.
    clauses = [f"COALESCE(window_end, created_at) > datetime('now','-{int(days)} days')"]
    if where:
        clauses.append(where)
    if project:
        clauses.append("project = ?")
        params = [*params, norm_project(project)]
    sql = f"""SELECT COALESCE(project,'(unscoped)') p, model, billing_mode,
                     SUM(turns) turns, SUM(input_tokens) input_tokens,
                     SUM(output_tokens) output_tokens, SUM(cache_read) cache_read,
                     SUM(cache_write) cache_write
              FROM usage_events WHERE {" AND ".join(clauses)}
              GROUP BY p, model, billing_mode ORDER BY output_tokens DESC"""
    rows = []
    billed_total = 0.0
    equivalent_total = 0.0
    unpriced: list[str] = []
    for r in db.execute(sql, params):
        usage = {k: r[k] for k in ("input_tokens", "output_tokens", "cache_read", "cache_write")}
        cost = pricing.cost_usd(r["model"], r["billing_mode"], usage)
        if cost["amount"] is not None:
            if cost.get("billed"):
                billed_total += cost["amount"]
            else:
                equivalent_total += cost["amount"]
        elif cost["reason"]:
            unpriced.append(f"{r['p']}/{r['model']}: {cost['reason']}")
        rows.append(
            {
                "project": r["p"],
                "model": r["model"],
                "billing_mode": r["billing_mode"],
                "turns": r["turns"],
                **usage,
                "cost": cost,
            }
        )
    return {
        "days": days,
        "rows": rows,
        # Two totals, not one. Metered work is an invoice; seat-billed work priced at
        # list is what the seat is worth. Both are useful, adding them is not.
        "billed_usd": round(billed_total, 4),
        "list_equivalent_usd": round(equivalent_total, 4),
        "not_priced": unpriced,
    }


@router.get("/by-session", summary="Token usage and cost per session")
async def usage_by_session(
    agent_id: str = ReaderDep,
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=500),
):
    db = get_db()
    where, params = project_filter(agent_id)
    clauses = [f"COALESCE(window_end, created_at) > datetime('now','-{int(days)} days')"]
    if where:
        clauses.append(where)
    rows = []
    for r in db.execute(
        f"""SELECT session_id, COALESCE(project,'(unscoped)') project, model, billing_mode,
                   SUM(turns) turns, SUM(input_tokens) input_tokens,
                   SUM(output_tokens) output_tokens, SUM(cache_read) cache_read,
                   SUM(cache_write) cache_write, MIN(window_start) started,
                   MAX(window_end) ended
            FROM usage_events WHERE {" AND ".join(clauses)}
            GROUP BY session_id, project, model, billing_mode
            ORDER BY output_tokens DESC LIMIT ?""",
        [*params, limit],
    ):
        usage = {k: r[k] for k in ("input_tokens", "output_tokens", "cache_read", "cache_write")}
        rows.append(
            {
                "session_id": r["session_id"],
                "project": r["project"],
                "model": r["model"],
                "turns": r["turns"],
                "started": r["started"],
                "ended": r["ended"],
                **usage,
                "cost": pricing.cost_usd(r["model"], r["billing_mode"], usage),
            }
        )
    return {"days": days, "rows": rows}


@router.get("/by-decision", summary="What each recorded decision cost to reach")
async def usage_by_decision(
    agent_id: str = ReaderDep,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=200),
):
    """Cost attributed to a decision through the session that produced it.

    The archivist mines decisions out of captures, so a decision carries the session
    it came from and that session's tokens are what it took to reach the choice. It is
    attribution by shared session, not proof of causation: a session that produced
    three decisions spent its tokens on all three and on everything else it did, so
    the figure is the cost of the work containing the decision, not of the decision
    alone. Shown per decision rather than divided, because dividing would invent a
    precision the data does not have.
    """
    db = get_db()
    where, params = project_filter(agent_id)
    clauses = [f"d.created_at > datetime('now','-{int(days)} days')", "d.session_id IS NOT NULL"]
    if where:
        clauses.append(where.replace("project", "d.project"))
    out = []
    for d in db.execute(
        f"""SELECT d.id, d.decision, d.rationale, d.alternatives, d.project,
                   d.session_id, d.created_at
            FROM decisions d WHERE {" AND ".join(clauses)}
            ORDER BY d.created_at DESC LIMIT ?""",
        [*params, limit],
    ):
        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0}
        amount = 0.0
        priced = False
        for u in db.execute(
            """SELECT model, billing_mode, SUM(input_tokens) input_tokens,
                      SUM(output_tokens) output_tokens, SUM(cache_read) cache_read,
                      SUM(cache_write) cache_write
               FROM usage_events WHERE session_id=? GROUP BY model, billing_mode""",
            (d["session_id"],),
        ):
            usage = {k: u[k] or 0 for k in totals}
            for k in totals:
                totals[k] += usage[k]
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
                "session_usage": totals,
                "session_cost": round(amount, 2) if priced else None,
            }
        )
    return {"days": days, "rows": out, "basis": "session containing the decision"}
