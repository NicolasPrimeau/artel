import json
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from ...store.db import AmbiguousId, get_db, norm_project, resolve_id
from ..auth import ActorDep, ReaderDep, _memberships, default_project_for, project_filter
from ..models import DecisionCreate, DecisionEntry, new_id

router = APIRouter(prefix="/decisions", tags=["decisions"])


def _resolve_decision(decision_id: str) -> str:
    try:
        resolved = resolve_id("decisions", decision_id)
    except AmbiguousId:
        raise HTTPException(status_code=400, detail="ambiguous decision id prefix")
    if resolved is None:
        raise HTTPException(status_code=404, detail="not found")
    return resolved


def _row_to_decision(row: sqlite3.Row) -> DecisionEntry:
    return DecisionEntry(
        id=row["id"],
        project=row["project"],
        agent_id=row["agent_id"],
        task_id=row["task_id"],
        decision=row["decision"],
        rationale=row["rationale"],
        alternatives=json.loads(row["alternatives"] or "[]"),
        created_at=row["created_at"],
    )


@router.post(
    "", response_model=DecisionEntry, status_code=201, summary="Record a decision (append-only)"
)
async def write_decision(body: DecisionCreate, agent_id: str = ActorDep):
    db = get_db()
    project = body.project if body.project is not None else default_project_for(agent_id)
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    decision_id = new_id()
    with db:
        db.execute(
            """INSERT INTO decisions (id, project, agent_id, task_id, decision, rationale, alternatives)
               VALUES (?,?,?,?,?,?,?)""",
            (
                decision_id,
                project,
                agent_id,
                body.task_id,
                body.decision,
                body.rationale,
                json.dumps(body.alternatives),
            ),
        )
    row = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    return _row_to_decision(row)


@router.get("", response_model=list[DecisionEntry], summary="List decisions")
async def list_decisions(
    project: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    agent_id: str = ReaderDep,
):
    project = norm_project(project)
    db = get_db()
    sql = "SELECT * FROM decisions WHERE 1=1"
    params: list = []
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            return []
        sql += " AND project=?"
        params.append(project)
    else:
        pf_clause, pf_params = project_filter(agent_id)
        if pf_clause:
            sql += f" AND {pf_clause}"
            params.extend(pf_params)
    if task_id:
        sql += " AND task_id=?"
        params.append(task_id)
    if agent:
        sql += " AND agent_id=?"
        params.append(agent)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [_row_to_decision(r) for r in rows]


@router.get("/{decision_id}", response_model=DecisionEntry, summary="Get a decision by ID")
async def get_decision(decision_id: str, agent_id: str = ReaderDep):
    decision_id = _resolve_decision(decision_id)
    db = get_db()
    row = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    return _row_to_decision(row)
