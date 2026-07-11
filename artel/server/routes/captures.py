from fastapi import APIRouter

from ...store.db import get_db, norm_project
from ..auth import ActorDep
from ..models import CaptureAck, CaptureCreate, new_id

router = APIRouter(prefix="/captures", tags=["captures"])

_MIN_TTL_HOURS = 1
_MAX_TTL_HOURS = 24 * 30


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
