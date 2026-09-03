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
    clauses = [f"created_at > datetime('now','-{int(days)} days')"]
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
    billable = 0.0
    unpriced: list[str] = []
    for r in db.execute(sql, params):
        usage = {k: r[k] for k in ("input_tokens", "output_tokens", "cache_read", "cache_write")}
        cost = pricing.cost_usd(r["model"], r["billing_mode"], usage)
        if cost["amount"] is not None:
            billable += cost["amount"]
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
        # Deliberately not a grand total. Mixing metered dollars with subscription
        # volume into one number invents spend that nobody is billed for.
        "billable_usd": round(billable, 4),
        "not_priced": unpriced,
    }
