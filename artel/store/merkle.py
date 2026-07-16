import hashlib
import sqlite3

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def entry_hash(
    entry_id: str,
    version: int,
    updated_at: str | None,
    deleted_at: str | None,
    vclock: str | None,
) -> str:
    raw = f"{entry_id}|{version}|{updated_at or ''}|{deleted_at or ''}|{vclock or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bucket_of(entry_id: str) -> str:
    return hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:2]


def _rows(db: sqlite3.Connection, project: str | None):
    return db.execute(
        "SELECT id, version, updated_at, deleted_at, vclock FROM memory"
        " WHERE project IS ? AND scope != 'agent'",
        (project,),
    ).fetchall()


def _hash_of(row) -> str:
    return entry_hash(
        row["id"], int(row["version"]), row["updated_at"], row["deleted_at"], row["vclock"]
    )


def bucket_entries(db: sqlite3.Connection, project: str | None, bucket: str) -> dict[str, str]:
    return {r["id"]: _hash_of(r) for r in _rows(db, project) if bucket_of(r["id"]) == bucket}


def tree(db: sqlite3.Connection, project: str | None) -> dict:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for r in _rows(db, project):
        grouped.setdefault(bucket_of(r["id"]), []).append((r["id"], _hash_of(r)))
    buckets: dict[str, str] = {}
    for b, pairs in grouped.items():
        pairs.sort()
        joined = "\n".join(f"{i}:{h}" for i, h in pairs)
        buckets[b] = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    joined = "\n".join(f"{b}:{h}" for b, h in sorted(buckets.items()))
    return {"root": hashlib.sha256(joined.encode("utf-8")).hexdigest(), "buckets": buckets}
