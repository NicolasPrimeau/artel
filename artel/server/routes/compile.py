import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from ...compile.install import installer_sh, standalone_hook
from ...store import graph as G
from ...store.db import fts_index, get_db, instance_id, norm_project
from ...store.embeddings import embed
from ..auth import (
    ActorDep,
    ReaderDep,
    _memberships,
    default_project_for,
    enforce_no_phantom_project,
)
from ..broadcast import broadcast
from ..config import settings
from ..models import (
    CompileCheckRequest,
    CompileCheckResult,
    CompileReport,
    CompileRequest,
    EdgeCreate,
    EventEntry,
    MemoryEntry,
    new_id,
)
from .memory import _row_to_entry

router = APIRouter(tags=["compile"])

COMPILED = "compiled"
TAG_COMPILED = "compiled"
EVENT_COMPILED = "memory.compiled"


def _base_url(request: Request) -> str:
    return settings.public_url or str(request.base_url).rstrip("/")


@router.get("/compile/install.sh", response_class=PlainTextResponse, include_in_schema=False)
async def compile_install(request: Request):
    return installer_sh(_base_url(request))


@router.get("/compile/hook.py", response_class=PlainTextResponse, include_in_schema=False)
async def compile_hook(request: Request):
    return standalone_hook(_base_url(request))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _scope_project(agent_id: str, project: str | None) -> str | None:
    project = project if project is not None else default_project_for(agent_id)
    enforce_no_phantom_project(agent_id, project)
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    return project


@router.post(
    "/compile",
    response_model=CompileReport,
    status_code=201,
    summary="Compile source into grounded memory",
)
async def compile_units(body: CompileRequest, agent_id: str = ActorDep):
    db = get_db()
    project = _scope_project(agent_id, body.project)
    created = updated = unchanged = 0
    memory_ids: list[str] = []
    resolved: list[tuple] = []
    changed_anchor_ids: list[str] = []
    invalidated: set[str] = set()
    event_id = new_id()

    with db:
        for unit in body.units:
            anchor_id, changed = G.upsert_anchor(
                db,
                project,
                unit.path,
                unit.symbol,
                unit.lang,
                unit.start_line,
                unit.end_line,
                unit.sha,
                body.commit,
            )
            resolved.append((unit, anchor_id, changed))
            if changed:
                changed_anchor_ids.append(anchor_id)

        for aid in changed_anchor_ids:
            invalidated |= G.invalidate(db, aid)

        for unit, anchor_id, changed in resolved:
            row = db.execute(
                "SELECT m.id FROM memory_edge e JOIN memory m ON m.id = e.src "
                "WHERE e.dst=? AND e.rel='grounds' AND m.deleted_at IS NULL LIMIT 1",
                (anchor_id,),
            ).fetchone()
            now = _now()
            if row is not None and not changed:
                memory_ids.append(row["id"])
                unchanged += 1
                continue
            if row is not None:
                mid = row["id"]
                db.execute(
                    "UPDATE memory SET content=?, source_path=?, source_sha=?, source_commit=?, "
                    "compiled_at=?, stale=0, version=version+1, updated_at=? WHERE id=?",
                    (unit.description, unit.path, unit.sha, body.commit, now, now, mid),
                )
                updated += 1
            else:
                mid = new_id()
                db.execute(
                    "INSERT INTO memory (id, type, agent_id, project, scope, content, confidence, "
                    "parents, tags, source_path, source_sha, source_commit, compiled_at, stale, "
                    "origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                    (
                        mid,
                        COMPILED,
                        agent_id,
                        project,
                        "project",
                        unit.description,
                        1.0,
                        "[]",
                        json.dumps([TAG_COMPILED, unit.lang or "src"]),
                        unit.path,
                        unit.sha,
                        body.commit,
                        now,
                        instance_id(),
                        now,
                        now,
                    ),
                )
                G.add_edge(db, project, mid, anchor_id, G.GROUNDS)
                created += 1
            memory_ids.append(mid)
            vec = embed(unit.description)
            if vec is not None:
                db.execute("DELETE FROM memory_vec WHERE id=?", (mid,))
                db.execute(
                    "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (mid, json.dumps(vec))
                )
            fts_index(db, mid, unit.description)
            G.remove_edges(db, mid, G.RELIES_ON)
            for dep in unit.deps:
                if dep.kind != "symbol":
                    continue
                target = G.find_anchor(db, project, unit.path, dep.name)
                if target is not None:
                    G.add_edge(db, project, mid, target["id"], G.RELIES_ON)

        if memory_ids:
            marks = ",".join("?" * len(memory_ids))
            db.execute(f"UPDATE memory SET stale=0 WHERE id IN ({marks})", memory_ids)
        payload = {"project": project, "count": len(memory_ids)}
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (event_id, EVENT_COMPILED, agent_id, json.dumps(payload)),
        )

    invalidated -= set(memory_ids)
    broadcast(
        EventEntry(
            id=event_id,
            type=EVENT_COMPILED,
            agent_id=agent_id,
            payload=payload,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
    )
    return CompileReport(
        project=project,
        anchors=len(resolved),
        created=created,
        updated=updated,
        unchanged=unchanged,
        invalidated=sorted(invalidated),
        memory_ids=memory_ids,
    )


@router.post(
    "/compile/check", response_model=list[CompileCheckResult], summary="SHA freshness check"
)
async def compile_check(body: CompileCheckRequest, agent_id: str = ReaderDep):
    db = get_db()
    project = norm_project(body.project)
    out: list[CompileCheckResult] = []
    for u in body.units:
        anchor = G.find_anchor(db, project, u.path, u.symbol)
        if anchor is None:
            status = "unknown"
        elif anchor["sha"] == u.sha:
            status = "fresh"
        else:
            status = "stale"
        out.append(CompileCheckResult(path=u.path, symbol=u.symbol, status=status))
    return out


@router.get(
    "/compile/stale", response_model=list[MemoryEntry], summary="Compiled nodes needing recompile"
)
async def compile_stale(project: str | None = Query(default=None), agent_id: str = ReaderDep):
    db = get_db()
    project = norm_project(project)
    rows = db.execute(
        "SELECT * FROM memory WHERE type='compiled' AND stale=1 AND deleted_at IS NULL "
        "AND (? IS NULL OR project=?) ORDER BY updated_at DESC LIMIT 200",
        (project, project),
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


@router.get("/compile/anchors", summary="List code anchors")
async def compile_anchors(
    project: str | None = Query(default=None),
    path: str | None = Query(default=None),
    agent_id: str = ReaderDep,
):
    db = get_db()
    project = norm_project(project)
    clauses = ["(? IS NULL OR project=?)"]
    params: list = [project, project]
    if path:
        clauses.append("path=?")
        params.append(path)
    rows = db.execute(
        f"SELECT * FROM code_anchor WHERE {' AND '.join(clauses)} ORDER BY path, start_line LIMIT 500",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/graph/{node_id}", summary="A graph node with its edges and viability")
async def graph_node(node_id: str, agent_id: str = ReaderDep):
    db = get_db()
    kind = G.node_kind(db, node_id)
    if kind is None:
        raise HTTPException(status_code=404, detail="node not found")
    if kind == "anchor":
        node = G.get_anchor(db, node_id)
    else:
        row = db.execute("SELECT * FROM memory WHERE id=?", (node_id,)).fetchone()
        node = dict(row)
    return {
        "id": node_id,
        "kind": kind,
        "node": node,
        "edges": G.edges_of(db, node_id),
        "viability": G.viability(db, node_id),
    }


@router.get("/graph", summary="List graph edges")
async def graph_edges(
    project: str | None = Query(default=None),
    rel: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    agent_id: str = ReaderDep,
):
    db = get_db()
    project = norm_project(project)
    clauses = ["(? IS NULL OR project=?)"]
    params: list = [project, project]
    if rel:
        clauses.append("rel=?")
        params.append(rel)
    rows = db.execute(
        f"SELECT id, project, src, dst, rel, note, created_at FROM memory_edge "
        f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/graph/edge", status_code=201, summary="Add a typed edge between two nodes")
async def graph_edge(body: EdgeCreate, agent_id: str = ActorDep):
    db = get_db()
    project = _scope_project(agent_id, body.project)
    if G.node_kind(db, body.src) is None or G.node_kind(db, body.dst) is None:
        raise HTTPException(status_code=404, detail="src or dst node not found")
    with db:
        edge_id = G.add_edge(db, project, body.src, body.dst, body.rel, body.note)
    return {"id": edge_id, "src": body.src, "dst": body.dst, "rel": body.rel}
