"""The fleet ledger: what the agents did, what it was worth, what was decided.

A separate app on its own port rather than another tab on /ui. The audience is
different — this is for whoever owns the budget, not whoever is operating the fleet —
and separating them keeps the read-only money view free of the agent controls.
"""

from __future__ import annotations

import pathlib

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from . import facts

app = FastAPI(title="Artel ledger", docs_url=None, redoc_url=None)
_PAGE = (pathlib.Path(__file__).parent / "page.html").read_text()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/projects")
async def projects(days: int = 7) -> dict:
    rows = facts.by_project(days)
    return {"days": days, "rows": rows, "totals": facts.totals(days)}


@app.get("/api/sessions")
async def sessions(days: int = 7, limit: int = 40) -> dict:
    return {"days": days, "rows": facts.by_session(days, limit)}


@app.get("/api/decisions")
async def decisions(days: int = 30, limit: int = 30) -> dict:
    return {"days": days, "rows": facts.by_decision(days, limit)}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE
