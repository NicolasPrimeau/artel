import json
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from ...store.db import get_db
from ..auth import ActorDep, ReaderDep
from ..broadcast import broadcast
from ..models import EventEntry, MessageEntry, MessageSend, new_id

router = APIRouter(prefix="/messages", tags=["messages"])


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


@router.post(
    "",
    response_model=MessageEntry,
    status_code=201,
    summary="Send a message to an agent or broadcast",
)
async def send_message(body: MessageSend, agent_id: str = ActorDep):
    from ..config import settings

    db = get_db()
    if body.to != "broadcast":
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
    sql = "SELECT * FROM messages WHERE (to_agent=? OR from_agent=?)"
    params: list = [agent_id, agent_id]
    if read is not None:
        sql += " AND read=?"
        params.append(1 if read else 0)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [_row_to_msg(r) for r in rows]


@router.get("/inbox", response_model=list[MessageEntry], summary="Fetch unread messages")
async def inbox(
    agent: str | None = Query(default=None),
    agent_id: str = ReaderDep,
):
    db = get_db()
    target = agent or agent_id
    rows = db.execute(
        """SELECT * FROM messages WHERE (
            (to_agent=? AND read=0) OR
            (to_agent='broadcast' AND id NOT IN (
                SELECT message_id FROM message_reads WHERE agent_id=?
            ))
        ) ORDER BY created_at DESC""",
        (target, target),
    ).fetchall()
    return [_row_to_msg(r) for r in rows]


@router.post("/inbox/read-all", summary="Mark all unread inbox messages as read")
async def mark_inbox_read(agent_id: str = ActorDep):
    db = get_db()
    with db:
        db.execute("UPDATE messages SET read=1 WHERE to_agent=? AND read=0", (agent_id,))
        db.execute(
            """INSERT OR IGNORE INTO message_reads (agent_id, message_id)
               SELECT ?, id FROM messages WHERE to_agent='broadcast'
               AND id NOT IN (SELECT message_id FROM message_reads WHERE agent_id=?)""",
            (agent_id, agent_id),
        )
    return {"ok": True}


@router.get("/{msg_id}", response_model=MessageEntry, summary="Fetch a single message by ID")
async def get_message(msg_id: str, agent_id: str = ReaderDep):
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if (
        row["to_agent"] != agent_id
        and row["from_agent"] != agent_id
        and row["to_agent"] != "broadcast"
    ):
        raise HTTPException(status_code=403, detail="forbidden")
    return _row_to_msg(row)


@router.post("/{msg_id}/read", response_model=MessageEntry, summary="Mark a message as read")
async def mark_read(msg_id: str, agent_id: str = ActorDep):
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["to_agent"] != agent_id and row["to_agent"] != "broadcast":
        raise HTTPException(status_code=403, detail="forbidden")
    if row["to_agent"] == "broadcast":
        with db:
            db.execute(
                "INSERT OR IGNORE INTO message_reads (agent_id, message_id) VALUES (?, ?)",
                (agent_id, msg_id),
            )
    else:
        with db:
            db.execute("UPDATE messages SET read=1 WHERE id=?", (msg_id,))
    row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return _row_to_msg(row)
