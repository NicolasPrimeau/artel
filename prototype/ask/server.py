"""Ask-your-fleet: an admin asks a question, the fleet's own record answers.

The demo Langfuse cannot run: it has traces, not a record of what the work produced.

Split by construction — arithmetic is computed in facts.py and handed to the model
already rendered; the model writes prose over it and cites entry ids. See llm.SYSTEM.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

import facts
import llm
import retrieve
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

DB = os.environ.get("ASK_DB", "/tmp/artel-ro.db")
app = FastAPI(title="Ask your fleet", docs_url=None, redoc_url=None)


class Question(BaseModel):
    question: str
    days: int = 7


def _facts_block(days: int) -> tuple[str, list[dict]]:
    rows = facts.fleet_table(DB, days)
    head = (
        f"Window: last {days} days.\n"
        f"{'project':14}{'sessions':>9}{'turns':>8}{'out_tokens':>12}"
        f"{'commits':>9}{'tok/commit':>12}{'notes':>7}{'decisions':>10}"
    )
    lines = [head]
    for r in rows:
        tpc = f"{r['tokens_per_commit']:,}" if r["tokens_per_commit"] is not None else "n/a"
        lines.append(
            f"{r['project']:14}{r['sessions']:>9}{r['turns']:>8}{r['output_tokens']:>12,}"
            f"{r['commits']:>9}{tpc:>12}{r['notes']:>7}{r['decisions']:>10}"
        )
    money = facts.cost_for(facts.usage_by_model(DB, days))
    lines.append("")
    lines.append(
        f"Cost: ${money['billed']:,.2f} billed (metered) · "
        f"${money['equivalent']:,.2f} list-price equivalent (seat-billed work). "
        "These are never added together."
    )
    if money["unpriced"]:
        lines.append(f"No published rate, therefore no figure: {', '.join(money['unpriced'])}")
    return "\n".join(lines), rows


@app.get("/api/fleet")
async def fleet(days: int = 7):
    block, rows = _facts_block(days)
    return {"days": days, "rows": rows, "rendered": block}


@app.post("/api/ask")
async def ask(q: Question):
    block, rows = _facts_block(q.days)
    try:
        passages = await retrieve.search(q.question, limit=8)
    except Exception as e:
        passages = []
        block += f"\n\n(retrieval unavailable: {e})"
    result = await llm.answer(q.question, block, passages)
    return JSONResponse(
        {
            "question": q.question,
            "facts": block,
            "fleet": rows,
            "passages": passages,
            "answer": result,
        }
    )


@app.get("/api/decisions")
async def decisions(days: int = 30, project: str | None = None, limit: int = 40):
    return {"rows": facts.decisions(DB, limit=limit, project=project)}


@app.get("/api/cost")
async def cost(days: int = 7, project: str | None = None):
    try:
        live = await retrieve.usage(
            "/usage", days=days, **({"project": project} if project else {})
        )
        rows = live["rows"]
        return {
            "days": days,
            "rows": rows,
            "live": True,
            "totals": {
                "billed": live.get("billed_usd", 0.0),
                "equivalent": live.get("list_equivalent_usd", 0.0),
                "unpriced": [n.split(": ")[0] for n in live.get("not_priced", [])],
            },
        }
    except Exception:
        # The snapshot is a fallback, never the primary: a stale cost figure that
        # looks live is worse than an obviously old one.
        rows = facts.usage_by_model(DB, days, project)
        return {"days": days, "rows": rows, "live": False, "totals": facts.cost_for(rows)}


@app.get("/api/cost/sessions")
async def cost_sessions(days: int = 7):
    try:
        return await retrieve.usage("/usage/by-session", days=days, limit=40)
    except Exception as e:
        return {"rows": [], "error": str(e)}


@app.get("/api/cost/decisions")
async def cost_decisions(days: int = 30):
    try:
        return await retrieve.usage("/usage/by-decision", days=days, limit=30)
    except Exception as e:
        return {"rows": [], "error": str(e)}


@app.get("/health")
async def health():
    ok = pathlib.Path(DB).exists()
    try:
        sqlite3.connect(f"file:{DB}?mode=ro", uri=True).execute("SELECT 1")
    except Exception as e:
        return {"status": "degraded", "db": str(e)}
    return {"status": "ok", "db_readable": ok, "dry_run": llm.dry_run(), "model": llm.MODEL}


PAGE = (pathlib.Path(__file__).parent / "page.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE
