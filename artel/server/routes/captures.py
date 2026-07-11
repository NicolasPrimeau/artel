from fastapi import APIRouter, Depends, Query

from ...store.db import get_db, norm_project
from ..auth import ActorDep, require_role
from ..models import CaptureAck, CaptureCreate, CaptureDigest, CaptureRecord, new_id

router = APIRouter(prefix="/captures", tags=["captures"])

# Reading raw session slices and draining the queue is internal plumbing — restricted
# to the archivist (and owners). Agents may only append their own captures.
ArchivistDep = Depends(require_role("archivist"))

_MIN_TTL_HOURS = 1
_MAX_TTL_HOURS = 24 * 30
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


@router.post(
    "",
    response_model=CaptureAck,
    status_code=201,
    summary="Append a raw session capture to the ingest queue",
)
async def create_capture(body: CaptureCreate, agent_id: str = ActorDep):
    # Cheap append onto the ingest queue: no embedding, no FTS, no broadcast, no mesh
    # feed. The archivist is the only path from here into memory. Keep this fast — it
    # is written by hooks off the agent's hot path and nothing waits on the result.
    db = get_db()
    capture_id = new_id()
    ttl = max(_MIN_TTL_HOURS, min(body.ttl_hours, _MAX_TTL_HOURS))
    db.execute(
        "INSERT INTO captures (id, agent_id, session_id, project, content, expires_at) "
        "VALUES (?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%fZ','now', ?))",
        (
            capture_id,
            agent_id,
            body.session_id,
            norm_project(body.project),
            body.content,
            f"+{ttl} hours",
        ),
    )
    db.commit()
    row = db.execute("SELECT id, expires_at FROM captures WHERE id=?", (capture_id,)).fetchone()
    return CaptureAck(id=row["id"], expires_at=row["expires_at"])


@router.get(
    "",
    response_model=list[CaptureRecord],
    summary="List pending captures for compaction (archivist only)",
)
async def list_pending(limit: int = Query(default=50, le=500), agent_id: str = ArchivistDep):
    db = get_db()
    # lazy prune: expired rows are dropped on read, so the queue stays bounded with no
    # separate job — a capture no one digested before its TTL is simply gone.
    db.execute(f"DELETE FROM captures WHERE expires_at IS NOT NULL AND expires_at < {_NOW}")
    db.commit()
    rows = db.execute(
        "SELECT id, agent_id, session_id, project, content, created_at FROM captures "
        "WHERE digested_at IS NULL ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall()
    return [CaptureRecord(**dict(r)) for r in rows]


@router.post("/digest", summary="Mark captures digested (archivist only)")
async def digest(body: CaptureDigest, agent_id: str = ArchivistDep):
    if not body.ids:
        return {"digested": 0}
    db = get_db()
    placeholders = ",".join("?" * len(body.ids))
    cur = db.execute(
        f"UPDATE captures SET digested_at={_NOW} "
        f"WHERE id IN ({placeholders}) AND digested_at IS NULL",
        body.ids,
    )
    db.commit()
    return {"digested": cur.rowcount}
