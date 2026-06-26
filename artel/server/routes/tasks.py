import json
import sqlite3

from fastapi import APIRouter, Body, HTTPException, Query

from ...store.db import AmbiguousId, get_db, norm_project, resolve_id
from ..auth import (
    ActorDep,
    ReaderDep,
    _memberships,
    default_project_for,
    enforce_no_phantom_project,
    is_archivist,
    is_owner,
    project_filter,
)
from ..models import (
    TaskAction,
    TaskComment,
    TaskCommentCreate,
    TaskCreate,
    TaskEntry,
    TaskUpdate,
    new_id,
)


def _fetch_deps(db: sqlite3.Connection, task_ids: list[str]) -> dict[str, list[str]]:
    if not task_ids:
        return {}
    placeholders = ",".join("?" * len(task_ids))
    rows = db.execute(
        f"SELECT task_id, depends_on FROM task_deps WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall()
    result: dict[str, list[str]] = {tid: [] for tid in task_ids}
    for r in rows:
        result[r["task_id"]].append(r["depends_on"])
    return result


router = APIRouter(prefix="/tasks", tags=["tasks"])


def _resolve_task(task_id: str) -> str:
    try:
        resolved = resolve_id("tasks", task_id)
    except AmbiguousId:
        raise HTTPException(status_code=400, detail="ambiguous task id prefix")
    if resolved is None:
        raise HTTPException(status_code=404, detail="not found")
    return resolved


def _require_project_membership(agent_id: str, project: str | None) -> None:
    if not project:
        return
    allowed = _memberships(agent_id)
    if allowed is not None and project not in allowed:
        raise HTTPException(status_code=403, detail="not a member of this project")


def _row_to_task(row: sqlite3.Row, depends_on: list[str] | None = None) -> TaskEntry:
    return TaskEntry(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        expected_outcome=row["expected_outcome"],
        status=row["status"],
        created_by=row["created_by"],
        assigned_to=row["assigned_to"],
        project=row["project"],
        priority=row["priority"],
        due_at=row["due_at"],
        tags=json.loads(row["tags"] or "[]"),
        depends_on=depends_on or [],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_comment(row: sqlite3.Row) -> TaskComment:
    return TaskComment(
        id=row["id"],
        task_id=row["task_id"],
        agent_id=row["agent_id"],
        kind=row["kind"],
        body=row["body"],
        created_at=row["created_at"],
    )


def _emit_event(db: sqlite3.Connection, event_type: str, agent_id: str, payload: dict) -> None:
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), event_type, agent_id, json.dumps(payload)),
    )


def _add_comment(db: sqlite3.Connection, task_id: str, agent_id: str, kind: str, body: str) -> None:
    db.execute(
        "INSERT INTO task_comments (id, task_id, agent_id, kind, body) VALUES (?,?,?,?,?)",
        (new_id(), task_id, agent_id, kind, body),
    )


@router.get("/{task_id}", response_model=TaskEntry, summary="Get a task by ID")
async def get_task(task_id: str, agent_id: str = ReaderDep):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    deps = _fetch_deps(db, [task_id])
    return _row_to_task(row, deps.get(task_id))


@router.post("", response_model=TaskEntry, status_code=201, summary="Create a task")
async def create_task(body: TaskCreate, agent_id: str = ActorDep):
    db = get_db()
    project = body.project if body.project is not None else default_project_for(agent_id)
    enforce_no_phantom_project(agent_id, project)
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    task_id = new_id()
    with db:
        db.execute(
            """INSERT INTO tasks (id, title, description, expected_outcome, created_by,
               project, priority, assigned_to, due_at, tags) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                body.title,
                body.description,
                body.expected_outcome,
                agent_id,
                project,
                body.priority,
                body.assigned_to,
                body.due_at,
                json.dumps(body.tags),
            ),
        )
        for dep_id in body.depends_on:
            db.execute(
                "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?,?)",
                (task_id, dep_id),
            )
        _emit_event(db, "task.created", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    deps = _fetch_deps(db, [task_id])
    return _row_to_task(row, deps.get(task_id))


@router.get("", response_model=list[TaskEntry], summary="List tasks with optional filters")
async def list_tasks(
    status: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    project: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    unblocked: bool = Query(default=False),
    agent_id: str = ReaderDep,
):
    project = norm_project(project)
    db = get_db()
    sql = "SELECT * FROM tasks WHERE 1=1"
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
    if status:
        sql += " AND status=?"
        params.append(status)
    if agent:
        sql += " AND (created_by=? OR assigned_to=?)"
        params.extend([agent, agent])
    if tag:
        sql += " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)"
        params.append(tag)
    if unblocked:
        sql += (
            " AND NOT EXISTS ("
            "SELECT 1 FROM task_deps d JOIN tasks dt ON dt.id=d.depends_on"
            " WHERE d.task_id=tasks.id AND dt.status!='completed')"
        )
    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql, params).fetchall()
    task_ids = [r["id"] for r in rows]
    deps_map = _fetch_deps(db, task_ids)
    return [_row_to_task(r, deps_map.get(r["id"])) for r in rows]


@router.post("/{task_id}/claim", response_model=TaskEntry, summary="Claim an open task")
async def claim_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = ActorDep,
):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    with db:
        cursor = db.execute(
            """UPDATE tasks SET status='claimed', assigned_to=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status='open'""",
            (agent_id, task_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=409, detail="task not open")
        _add_comment(db, task_id, agent_id, "claim", body.body)
        _emit_event(db, "task.claimed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/unclaim",
    response_model=TaskEntry,
    summary="Release your claim on a task (assignee only)",
)
async def unclaim_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = ActorDep,
):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail="task not claimed")
    if row["assigned_to"] != agent_id and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="forbidden")
    _require_project_membership(agent_id, row["project"])
    with db:
        db.execute(
            """UPDATE tasks SET status='open', assigned_to=NULL,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (task_id,),
        )
        _add_comment(db, task_id, agent_id, "unclaim", body.body)
        _emit_event(db, "task.unclaimed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/complete",
    response_model=TaskEntry,
    summary="Complete a claimed task (assignee only)",
)
async def complete_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = ActorDep,
):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail="task not claimed")
    if row["assigned_to"] != agent_id and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="forbidden")
    _require_project_membership(agent_id, row["project"])
    with db:
        db.execute(
            """UPDATE tasks SET status='completed',
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (task_id,),
        )
        _add_comment(db, task_id, agent_id, "complete", body.body)
        _emit_event(db, "task.completed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.patch(
    "/{task_id}", response_model=TaskEntry, summary="Update task title, description, or priority"
)
async def update_task(task_id: str, body: TaskUpdate, agent_id: str = ActorDep):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] in ("completed", "failed"):
        raise HTTPException(status_code=409, detail="task is terminal and cannot be modified")
    is_task_actor = (
        row["created_by"] == agent_id
        or row["assigned_to"] == agent_id
        or is_owner(agent_id)
        or is_archivist(agent_id)
    )
    tag_only = (
        body.tags is not None
        and body.description is None
        and body.title is None
        and body.priority is None
        and body.expected_outcome is None
        and body.project is None
    )
    if not is_task_actor:
        if tag_only and row["project"]:
            allowed = _memberships(agent_id)
            if allowed is not None and row["project"] not in allowed:
                raise HTTPException(status_code=403, detail="forbidden")
        elif not tag_only:
            raise HTTPException(status_code=403, detail="forbidden")
    if not is_owner(agent_id) and not is_archivist(agent_id):
        _require_project_membership(agent_id, row["project"])
    set_parts: list[str] = []
    params: list = []
    if body.description is not None:
        if body.append:
            set_parts.append(
                "description = CASE WHEN description IS NOT NULL AND description != '' "
                "THEN description || ? ELSE ? END"
            )
            params.extend([f"\n\n---\n{body.description}", body.description])
        else:
            set_parts.append("description=?")
            params.append(body.description)
    if body.title is not None:
        set_parts.append("title=?")
        params.append(body.title)
    if body.priority is not None:
        set_parts.append("priority=?")
        params.append(body.priority)
    if body.expected_outcome is not None:
        set_parts.append("expected_outcome=?")
        params.append(body.expected_outcome)
    if body.project is not None:
        enforce_no_phantom_project(agent_id, body.project)
        allowed = _memberships(agent_id)
        if allowed is not None and body.project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of target project")
        set_parts.append("project=?")
        params.append(body.project)
    if body.tags is not None:
        set_parts.append("tags=?")
        params.append(json.dumps(body.tags))
    if set_parts:
        set_parts.append("updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        params.append(task_id)
        with db:
            db.execute(f"UPDATE tasks SET {', '.join(set_parts)} WHERE id=?", params)
            _emit_event(db, "task.updated", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/fail", response_model=TaskEntry, summary="Fail a claimed task (assignee only)"
)
async def fail_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = ActorDep,
):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail="task not claimed")
    if row["assigned_to"] != agent_id and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="forbidden")
    _require_project_membership(agent_id, row["project"])
    with db:
        db.execute(
            """UPDATE tasks SET status='failed',
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (task_id,),
        )
        _add_comment(db, task_id, agent_id, "fail", body.body)
        _emit_event(db, "task.failed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/comments",
    response_model=TaskComment,
    status_code=201,
    summary="Add a free-form comment to a task",
)
async def add_comment(task_id: str, body: TaskCommentCreate, agent_id: str = ActorDep):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    comment_id = new_id()
    with db:
        db.execute(
            "INSERT INTO task_comments (id, task_id, agent_id, kind, body) VALUES (?,?,?,?,?)",
            (comment_id, task_id, agent_id, "comment", body.body),
        )
        _emit_event(db, "task.commented", agent_id, {"task_id": task_id, "comment_id": comment_id})
    crow = db.execute("SELECT * FROM task_comments WHERE id=?", (comment_id,)).fetchone()
    return _row_to_comment(crow)


@router.get(
    "/{task_id}/comments",
    response_model=list[TaskComment],
    summary="List comments and status events for a task",
)
async def list_comments(task_id: str, agent_id: str = ReaderDep):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    rows = db.execute(
        "SELECT * FROM task_comments WHERE task_id=? ORDER BY created_at ASC",
        (task_id,),
    ).fetchall()
    return [_row_to_comment(r) for r in rows]


@router.post(
    "/{task_id}/dependencies",
    response_model=TaskEntry,
    status_code=201,
    summary="Add a dependency to a task (task will be blocked until the dependency is completed)",
)
async def add_dependency(
    task_id: str, depends_on: str = Body(..., embed=True), agent_id: str = ActorDep
):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    _require_project_membership(agent_id, row["project"])
    if not db.execute("SELECT 1 FROM tasks WHERE id=?", (depends_on,)).fetchone():
        raise HTTPException(status_code=404, detail="dependency task not found")
    if depends_on == task_id:
        raise HTTPException(status_code=400, detail="task cannot depend on itself")
    with db:
        db.execute(
            "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?,?)",
            (task_id, depends_on),
        )
    deps = _fetch_deps(db, [task_id])
    return _row_to_task(row, deps.get(task_id))


@router.delete(
    "/{task_id}/dependencies/{dep_id}",
    response_model=TaskEntry,
    summary="Remove a dependency from a task",
)
async def remove_dependency(task_id: str, dep_id: str, agent_id: str = ActorDep):
    task_id = _resolve_task(task_id)
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    _require_project_membership(agent_id, row["project"])
    with db:
        db.execute(
            "DELETE FROM task_deps WHERE task_id=? AND depends_on=?",
            (task_id, dep_id),
        )
    deps = _fetch_deps(db, [task_id])
    return _row_to_task(row, deps.get(task_id))
