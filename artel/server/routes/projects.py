from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...store.db import get_db, norm_project
from ..auth import ActorDep, ReaderDep, is_archivist, is_owner
from ..config import settings
from ..models import ProjectCreate, ProjectInfo

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectMember(BaseModel):
    agent_id: str
    role: str = "member"
    joined_at: str


class ProjectSummary(BaseModel):
    project_id: str
    joined_at: str


def _project_role(db, project_id: str, agent_id: str) -> str | None:
    row = db.execute(
        "SELECT role FROM project_members WHERE project_id=? AND agent_id=?",
        (project_id, agent_id),
    ).fetchone()
    return row["role"] if row else None


def _role_for_new_member(db, project_id: str, agent_id: str) -> str:
    existing = _project_role(db, project_id, agent_id)
    if existing:
        return existing  # preserve role (e.g. an owner re-joining stays owner)
    has_members = db.execute(
        "SELECT 1 FROM project_members WHERE project_id=? LIMIT 1", (project_id,)
    ).fetchone()
    return "member" if has_members else "owner"  # first member owns the project


@router.post("", status_code=204, summary="Create a project and join it")
async def create_project(body: ProjectCreate, agent_id: str = ActorDep):
    if is_archivist(agent_id):
        raise HTTPException(status_code=403, detail="archivist cannot create projects")
    db = get_db()
    with db:
        role = _role_for_new_member(db, body.name, agent_id)
        db.execute(
            "INSERT OR IGNORE INTO project_members (project_id, agent_id, role) VALUES (?, ?, ?)",
            (body.name, agent_id, role),
        )


@router.post("/{project_id}/join", status_code=204, summary="Join a project (replaces current)")
async def join_project(project_id: str, agent_id: str = ActorDep):
    if is_archivist(agent_id):
        raise HTTPException(status_code=403, detail="archivist cannot join projects")
    project_id = norm_project(project_id) or ""
    if not project_id:
        raise HTTPException(status_code=422, detail="project name required")
    db = get_db()
    with db:
        role = _role_for_new_member(db, project_id, agent_id)
        db.execute("DELETE FROM project_members WHERE agent_id=?", (agent_id,))
        db.execute(
            "INSERT INTO project_members (project_id, agent_id, role) VALUES (?, ?, ?)",
            (project_id, agent_id, role),
        )


@router.post(
    "/{project_id}/clear",
    status_code=204,
    summary="Clear all memory in a project (an owner of the project, only)",
)
async def clear_project(project_id: str, agent_id: str = ActorDep):
    project_id = norm_project(project_id) or ""
    if not project_id:
        raise HTTPException(status_code=422, detail="project name required")
    db = get_db()
    if _project_role(db, project_id, agent_id) != "owner" and not is_owner(agent_id):
        raise HTTPException(status_code=403, detail="only a project owner can clear it")
    now = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
    with db:
        db.execute(
            f"UPDATE memory SET deleted_at={now} WHERE project=? AND deleted_at IS NULL",
            (project_id,),
        )


@router.delete("/{project_id}/leave", status_code=204, summary="Leave a project")
async def leave_project(project_id: str, agent_id: str = ActorDep):
    project_id = norm_project(project_id) or ""
    db = get_db()
    db.execute(
        "DELETE FROM project_members WHERE project_id=? AND agent_id=?",
        (project_id, agent_id),
    )
    db.commit()


@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMember],
    summary="List members of a project",
)
async def list_members(project_id: str, agent_id: str = ReaderDep):
    project_id = norm_project(project_id) or ""
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM project_members WHERE project_id=? AND agent_id=?",
        (project_id, agent_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="not a member of this project")
    rows = db.execute(
        "SELECT agent_id, role, joined_at FROM project_members WHERE project_id=? ORDER BY joined_at",
        (project_id,),
    ).fetchall()
    return [
        ProjectMember(agent_id=r["agent_id"], role=r["role"], joined_at=r["joined_at"])
        for r in rows
    ]


@router.get("/mine", response_model=list[ProjectSummary], summary="List projects you belong to")
async def list_my_projects(agent_id: str = ReaderDep):
    db = get_db()
    rows = db.execute(
        "SELECT project_id, joined_at FROM project_members WHERE agent_id=? ORDER BY joined_at",
        (agent_id,),
    ).fetchall()
    return [ProjectSummary(project_id=r["project_id"], joined_at=r["joined_at"]) for r in rows]


@router.get("", response_model=list[ProjectInfo])
async def list_projects(agent_id: str = ReaderDep):
    db = get_db()

    projects: dict[str, dict] = {}

    def _ensure(name: str) -> dict:
        if name not in projects:
            projects[name] = {
                "agents": set(),
                "memory_count": 0,
                "task_count": 0,
                "last_activity": None,
            }
        return projects[name]

    for row in db.execute(
        "SELECT project, agent_id, COUNT(*) as cnt, MAX(updated_at) as last FROM memory "
        "WHERE project IS NOT NULL AND deleted_at IS NULL GROUP BY project, agent_id"
    ).fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["agent_id"])
        p["memory_count"] += row["cnt"]
        if not p["last_activity"] or row["last"] > p["last_activity"]:
            p["last_activity"] = row["last"]

    for row in db.execute(
        "SELECT project, created_by, COUNT(*) as cnt, MAX(updated_at) as last FROM tasks "
        "WHERE project IS NOT NULL GROUP BY project, created_by"
    ).fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["created_by"])
        p["task_count"] += row["cnt"]
        if not p["last_activity"] or row["last"] > p["last_activity"]:
            p["last_activity"] = row["last"]

    for row in db.execute("SELECT id, project FROM agents WHERE project IS NOT NULL").fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["id"])

    for agent_id_cfg, proj_list in settings.agent_projects().items():
        for proj in proj_list:
            p = _ensure(proj)
            p["agents"].add(agent_id_cfg)

    for row in db.execute("SELECT project_id, agent_id FROM project_members").fetchall():
        p = _ensure(row["project_id"])
        p["agents"].add(row["agent_id"])

    return [
        ProjectInfo(
            name=name,
            agents=sorted(data["agents"]),
            memory_count=data["memory_count"],
            task_count=data["task_count"],
            last_activity=data["last_activity"],
        )
        for name, data in sorted(projects.items())
    ]
