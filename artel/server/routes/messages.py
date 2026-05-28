import json
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from ...store.db import get_db, norm_project
from ..auth import ActorDep, ReaderDep, _memberships, is_owner
from ..broadcast import broadcast
from ..models import EventEntry, MessageEntry, MessageSend, new_id

router = APIRouter(prefix="/messages", tags=["messages"])

_PROJECT_PREFIX = "project:"


def _row_to_msg(row: sqlite3.Row) -> MessageEntry:
    return MessageEntry(
        id=row["id"],
        from_agent=row["from_agent"],
        to_agent=row["to_agent"],
        subject=row["subject"],
        body=row["body"],
        read=bool(row["read"]),
        created_at=row["created_at"],
    )


def _project_inbox_targets(agent_id: str) -> list[str]:
    """Return ['project:<p>', ...] targets the agent can read."""
    allowed = _memberships(agent_id)
    db = get_db()
    if allowed is None:
        rows = db.execute("SELECT DISTINCT project_id FROM project_members").fetchall()
        return [f"{_PROJECT_PREFIX}{r['project_id']}" for r in rows]
    return [f"{_PROJECT_PREFIX}{p}" for p in allowed]


def _project_exists(project: str) -> bool:
    from ..config import settings

    db = get_db()
    row = db.execute(
        "SELECT 1 FROM project_members WHERE project_id=? LIMIT 1", (project,)
    ).fetchone()
    if row:
        return True
    for proj_list in settings.agent_projects().values():
        if project in (proj_list or []):
            return True
    return False


def _can_post_to_project(agent_id: str, project: str) -> bool:
    if is_owner(agent_id):
        return True
    allowed = _memberships(agent_id)
    if allowed is None:
        return True
    return project in allowed


def _shared_inbox_predicate(agent_id: str, targets: list[str]) -> tuple[str, list]:
    """SQL fragment + params matching unread broadcast-style messages.

    Covers the broadcast pseudo-target plus any project:<p> targets passed in.
    Read-tracking uses message_reads (per-recipient), same as broadcasts.
    """
    in_targets = ["broadcast", *targets]
    placeholders = ",".join("?" * len(in_targets))
    sql = (
        f"(to_agent IN ({placeholders}) AND id NOT IN "
        f"(SELECT message_id FROM message_reads WHERE agent_id=?))"
    )
    return sql, [*in_targets, agent_id]


@router.post(
    "",
    response_model=MessageEntry,
    status_code=201,
    summary="Send a message to an agent, project, or broadcast",
)
async def send_message(body: MessageSend, agent_id: str = ActorDep):
    from ..config import settings

    db = get_db()
    if body.to.startswith(_PROJECT_PREFIX):
        project = norm_project(body.to[len(_PROJECT_PREFIX) :])
        if not project:
            raise HTTPException(status_code=400, detail="project name required after 'project:'")
        body.to = f"{_PROJECT_PREFIX}{project}"
        if not _project_exists(project):
            raise HTTPException(status_code=404, detail="project not found")
        if not _can_post_to_project(agent_id, project):
            raise HTTPException(status_code=403, detail="not a member of this project")
    elif body.to != "broadcast":
        in_db = db.execute("SELECT id FROM agents WHERE id=?", (body.to,)).fetchone()
        in_config = body.to in settings.api_keys().values()
        if not in_db and not in_config:
            raise HTTPException(status_code=404, detail="recipient not found")
    msg_id = new_id()
    event_id = new_id()
    with db:
        db.execute(
            "INSERT INTO messages (id, from_agent, to_agent, subject, body) VALUES (?,?,?,?,?)",
            (msg_id, agent_id, body.to, body.subject, body.body),
        )
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (
                event_id,
                "message.received",
                agent_id,
                json.dumps({"message_id": msg_id, "to": body.to}),
            ),
        )

    broadcast(
        EventEntry(
            id=event_id,
            type="message.received",
            agent_id=agent_id,
            payload={"message_id": msg_id, "to": body.to},
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
    )

    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)


@router.get("", response_model=list[MessageEntry], summary="List all sent and received messages")
async def list_messages(
    read: bool | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    agent_id: str = ReaderDep,
):
    db = get_db()
    project_targets = _project_inbox_targets(agent_id)
    placeholders = ",".join("?" * (1 + len(project_targets))) if project_targets else "?"
    sql = f"SELECT * FROM messages WHERE (to_agent IN ({placeholders}) OR from_agent=?)"
    params: list = [agent_id, *project_targets, agent_id]
    if read is not None:
        sql += " AND read=?"
        params.append(1 if read else 0)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [_row_to_msg(r) for r in rows]


@router.get("/inbox", response_model=list[MessageEntry], summary="Fetch unread messages")
async def inbox(agent_id: str = ReaderDep):
    db = get_db()
    shared_sql, shared_params = _shared_inbox_predicate(agent_id, _project_inbox_targets(agent_id))
    rows = db.execute(
        f"""SELECT * FROM messages WHERE (
            (to_agent=? AND read=0) OR {shared_sql}
        ) ORDER BY created_at DESC""",
        [agent_id, *shared_params],
    ).fetchall()
    return [_row_to_msg(r) for r in rows]


@router.post(
    "/inbox/consume",
    response_model=list[MessageEntry],
    summary="Atomically fetch and mark unread messages as read",
)
async def consume_inbox(agent_id: str = ActorDep):
    db = get_db()
    project_targets = _project_inbox_targets(agent_id)
    shared_targets = {"broadcast", *project_targets}
    shared_sql, shared_params = _shared_inbox_predicate(agent_id, project_targets)
    with db:
        rows = db.execute(
            f"""SELECT * FROM messages WHERE (
                (to_agent=? AND read=0) OR {shared_sql}
            ) ORDER BY created_at DESC""",
            [agent_id, *shared_params],
        ).fetchall()
        if rows:
            direct_ids = [r["id"] for r in rows if r["to_agent"] == agent_id]
            shared_ids = [r["id"] for r in rows if r["to_agent"] in shared_targets]
            if direct_ids:
                db.execute(
                    f"UPDATE messages SET read=1 WHERE id IN ({','.join('?' * len(direct_ids))})",
                    direct_ids,
                )
            for mid in shared_ids:
                db.execute(
                    "INSERT OR IGNORE INTO message_reads (agent_id, message_id) VALUES (?, ?)",
                    (agent_id, mid),
                )
    return [_row_to_msg(r) for r in rows]


@router.post("/inbox/read-all", summary="Mark all unread inbox messages as read")
async def mark_inbox_read(agent_id: str = ActorDep):
    db = get_db()
    project_targets = _project_inbox_targets(agent_id)
    shared_targets = ["broadcast", *project_targets]
    placeholders = ",".join("?" * len(shared_targets))
    with db:
        db.execute("UPDATE messages SET read=1 WHERE to_agent=? AND read=0", (agent_id,))
        db.execute(
            f"""INSERT OR IGNORE INTO message_reads (agent_id, message_id)
               SELECT ?, id FROM messages WHERE to_agent IN ({placeholders})
               AND id NOT IN (SELECT message_id FROM message_reads WHERE agent_id=?)""",
            [agent_id, *shared_targets, agent_id],
        )
    return {"ok": True}


def _can_read_message(row: sqlite3.Row, agent_id: str) -> bool:
    to_agent = row["to_agent"]
    if to_agent == agent_id or row["from_agent"] == agent_id or to_agent == "broadcast":
        return True
    if to_agent and to_agent.startswith(_PROJECT_PREFIX):
        project = to_agent[len(_PROJECT_PREFIX) :]
        return _can_post_to_project(agent_id, project)
    return False


@router.get("/{msg_id}", response_model=MessageEntry, summary="Fetch a single message by ID")
async def get_message(msg_id: str, agent_id: str = ReaderDep):
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if not _can_read_message(row, agent_id):
        raise HTTPException(status_code=403, detail="forbidden")
    return _row_to_msg(row)


@router.post("/{msg_id}/read", response_model=MessageEntry, summary="Mark a message as read")
async def mark_read(msg_id: str, agent_id: str = ActorDep):
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    to_agent = row["to_agent"]
    is_direct = to_agent == agent_id
    is_shared = to_agent == "broadcast" or (
        to_agent
        and to_agent.startswith(_PROJECT_PREFIX)
        and _can_post_to_project(agent_id, to_agent[len(_PROJECT_PREFIX) :])
    )
    if not is_direct and not is_shared:
        raise HTTPException(status_code=403, detail="forbidden")
    with db:
        if is_direct:
            db.execute("UPDATE messages SET read=1 WHERE id=?", (msg_id,))
        else:
            db.execute(
                "INSERT OR IGNORE INTO message_reads (agent_id, message_id) VALUES (?, ?)",
                (agent_id, msg_id),
            )
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)
