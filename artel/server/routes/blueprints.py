import json
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from ...store.db import get_db, norm_project
from ..auth import (
    ActorDep,
    ReaderDep,
    _memberships,
    default_project_for,
    enforce_no_phantom_project,
)
from ..blueprint import (
    BlueprintCreate,
    BlueprintDocument,
    BlueprintInstantiate,
    lowered_fraction,
    validate_document,
)
from ..contract import validate_contract
from ..models import BlueprintEntry, BlueprintRunEntry, BlueprintRunNode, new_id
from ..reactor import start_run

router = APIRouter(prefix="/blueprints", tags=["blueprints"])


def _require_membership(agent_id: str, project: str | None) -> None:
    if not project:
        return
    allowed = _memberships(agent_id)
    if allowed is not None and project not in allowed:
        raise HTTPException(status_code=403, detail="not a member of this project")


def _row_to_blueprint(row: sqlite3.Row) -> BlueprintEntry:
    doc = BlueprintDocument(**json.loads(row["document"]))
    lowered = sum(1 for n in doc.nodes if n.run is not None)
    return BlueprintEntry(
        node_count=len(doc.nodes),
        lowered_nodes=lowered,
        lowered_fraction=round(lowered_fraction(doc), 3),
        id=row["id"],
        name=row["name"],
        project=row["project"],
        document=json.loads(row["document"]),
        created_by=row["created_by"],
        version=row["version"],
        source_entry_id=row["source_entry_id"],
        source_version=row["source_version"],
        created_at=row["created_at"],
    )


def _current(db: sqlite3.Connection, name: str, project: str | None) -> sqlite3.Row | None:
    return db.execute(
        """SELECT * FROM blueprints WHERE name=? AND superseded_at IS NULL
           AND (project IS ? OR project=?) ORDER BY version DESC LIMIT 1""",
        (name, project, project),
    ).fetchone()


@router.post("", response_model=BlueprintEntry, status_code=201, summary="Compile a blueprint")
async def create_blueprint(body: BlueprintCreate, agent_id: str = ActorDep):
    doc = body.document
    problems = validate_document(doc)
    for node in doc.nodes:
        if node.completion_contract is not None:
            problems.extend(f"{node.id}: {p}" for p in validate_contract(node.completion_contract))
    if problems:
        raise HTTPException(status_code=422, detail={"blueprint": problems})
    db = get_db()
    project = norm_project(body.project) if body.project else default_project_for(agent_id)
    enforce_no_phantom_project(agent_id, project)
    _require_membership(agent_id, project)
    existing = _current(db, doc.name, project)
    blueprint_id = new_id()
    with db:
        if existing:
            db.execute(
                """UPDATE blueprints SET superseded_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id=?""",
                (existing["id"],),
            )
        db.execute(
            """INSERT INTO blueprints (id, name, project, document, created_by, version,
               source_entry_id, source_version) VALUES (?,?,?,?,?,?,?,?)""",
            (
                blueprint_id,
                doc.name,
                project,
                doc.model_dump_json(),
                agent_id,
                (existing["version"] + 1) if existing else 1,
                body.source_entry_id,
                body.source_version,
            ),
        )
    row = db.execute("SELECT * FROM blueprints WHERE id=?", (blueprint_id,)).fetchone()
    return _row_to_blueprint(row)


@router.get("", response_model=list[BlueprintEntry], summary="List current blueprints")
async def list_blueprints(project: str | None = Query(default=None), agent_id: str = ReaderDep):
    db = get_db()
    project = norm_project(project)
    sql = "SELECT * FROM blueprints WHERE superseded_at IS NULL"
    params: list = []
    if project:
        sql += " AND project=?"
        params.append(project)
    sql += " ORDER BY name ASC"
    rows = db.execute(sql, params).fetchall()
    allowed = _memberships(agent_id)
    return [
        _row_to_blueprint(r)
        for r in rows
        if not r["project"] or allowed is None or r["project"] in allowed
    ]


@router.post(
    "/{name}/instantiate",
    response_model=BlueprintRunEntry,
    status_code=201,
    summary="Instantiate a blueprint — materializes its root tasks and starts the reactor run",
)
async def instantiate_blueprint(name: str, body: BlueprintInstantiate, agent_id: str = ActorDep):
    db = get_db()
    project = norm_project(body.project) if body.project else default_project_for(agent_id)
    enforce_no_phantom_project(agent_id, project)
    _require_membership(agent_id, project)
    row = _current(db, name, project)
    if not row:
        raise HTTPException(status_code=404, detail="blueprint not found")
    doc = BlueprintDocument(**json.loads(row["document"]))
    missing = [p for p in doc.params if p not in body.params]
    if missing:
        raise HTTPException(status_code=422, detail={"params": [f"missing: {m}" for m in missing]})
    with db:
        run_id = start_run(db, row, doc, body.params, project, agent_id)
    return await get_run(run_id, agent_id)


@router.get(
    "/runs/{run_id}",
    response_model=BlueprintRunEntry,
    summary="Get a blueprint run and the tasks materialized so far",
)
async def get_run(run_id: str, agent_id: str = ReaderDep):
    db = get_db()
    row = db.execute("SELECT * FROM blueprint_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    _require_membership(agent_id, row["project"])
    nodes = db.execute(
        """SELECT n.node_id, n.task_id, n.item, n.superseded, t.title, t.status
           FROM blueprint_run_nodes n JOIN tasks t ON t.id=n.task_id
           WHERE n.run_id=? ORDER BY n.created_at ASC""",
        (run_id,),
    ).fetchall()
    return BlueprintRunEntry(
        id=row["id"],
        blueprint_id=row["blueprint_id"],
        name=row["name"],
        params=json.loads(row["params"] or "{}"),
        project=row["project"],
        created_by=row["created_by"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        nodes=[
            BlueprintRunNode(
                node_id=n["node_id"],
                task_id=n["task_id"],
                title=n["title"],
                status=n["status"],
                item=json.loads(n["item"]) if n["item"] else None,
                superseded=bool(n["superseded"]),
            )
            for n in nodes
        ],
    )
