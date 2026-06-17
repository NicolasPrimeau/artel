import json

from fastapi import APIRouter, Depends

from ...store.db import get_db
from ..auth import require_role
from ..models import LeaseRequest, LeaseResponse

router = APIRouter(prefix="/archivist", tags=["archivist"])

ArchivistDep = Depends(require_role("archivist"))

_LEASE_KEY = "archivist_lease"


@router.post(
    "/lease", response_model=LeaseResponse, summary="Acquire or renew the archivist singleton lease"
)
async def acquire_lease(body: LeaseRequest, agent_id: str = ArchivistDep):
    db = get_db()
    with db:
        db.execute(
            """
            INSERT INTO kv (key, value)
            VALUES (
                ?,
                json_object(
                    'holder', ?,
                    'expires_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
                )
            )
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            WHERE json_extract(kv.value, '$.holder') = ?
               OR json_extract(kv.value, '$.expires_at') <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (_LEASE_KEY, body.instance_id, f"+{body.ttl_seconds} seconds", body.instance_id),
        )
    row = db.execute("SELECT value FROM kv WHERE key=?", (_LEASE_KEY,)).fetchone()
    current = json.loads(row["value"])
    return LeaseResponse(
        granted=current["holder"] == body.instance_id,
        holder=current["holder"],
        expires_at=current["expires_at"],
    )
