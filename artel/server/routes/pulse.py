import json

from fastapi import APIRouter, Query

from ...store import affinity, decay, graph, hebbian
from ...store.db import get_db, norm_project
from ..auth import ReaderDep
from .memory import _TRAIL_HALF_LIFE_DAYS

router = APIRouter(prefix="/pulse", tags=["pulse"])

_MAX_EDGES = 400


def _label(row) -> str:
    text = row["headline"] or row["content"]
    return " ".join(str(text or "").split())[:110]


def _hydrate(db, ids: set[str], project: str | None) -> dict[str, dict]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    clause = " AND project=?" if project else ""
    params = [*ids, *([project] if project else [])]
    rows = db.execute(
        f"SELECT id, content, headline, type, trail, trail_at FROM memory"
        f" WHERE id IN ({placeholders}) AND deleted_at IS NULL AND scope != 'agent'{clause}",
        params,
    ).fetchall()
    return {r["id"]: r for r in rows}


@router.get("", summary="Fleet pulse: knowledge graph, trails, centrality, affinities, conflicts")
async def pulse(project: str | None = Query(default=None), agent_id: str = ReaderDep):
    project = norm_project(project)
    db = get_db()

    sem_clause = " WHERE project=?" if project else ""
    sem_params = [project] if project else []
    sem = db.execute(
        f"SELECT src, dst, rel FROM memory_edge{sem_clause} ORDER BY created_at DESC LIMIT ?",
        [*sem_params, _MAX_EDGES],
    ).fetchall()

    heb = []
    for r in db.execute(
        "SELECT src, dst, weight, updated_at FROM hebbian_edge ORDER BY updated_at DESC LIMIT ?",
        (_MAX_EDGES,),
    ).fetchall():
        w = decay.decayed(r["weight"], r["updated_at"], hebbian.HALF_LIFE_DAYS)
        if w >= hebbian.MIN_WEIGHT:
            heb.append((r["src"], r["dst"], round(w, 3)))

    node_ids = {r["src"] for r in sem} | {r["dst"] for r in sem}
    node_ids |= {s for s, _, _ in heb} | {d for _, d, _ in heb}
    known = _hydrate(db, node_ids, project)

    nodes = [
        {
            "id": nid,
            "label": _label(row),
            "type": row["type"],
            "trail": round(
                decay.decayed(row["trail"] or 0.0, row["trail_at"], _TRAIL_HALF_LIFE_DAYS), 2
            ),
        }
        for nid, row in known.items()
    ]
    edges = [
        {"src": r["src"], "dst": r["dst"], "rel": r["rel"], "kind": "semantic"}
        for r in sem
        if r["src"] in known and r["dst"] in known
    ] + [
        {"src": s, "dst": d, "rel": "co-retrieved", "kind": "hebbian", "weight": w}
        for s, d, w in heb
        if s in known and d in known
    ]

    trail_rows = db.execute(
        f"SELECT id, content, headline, type, trail, trail_at FROM memory"
        f" WHERE trail > 0 AND deleted_at IS NULL AND scope != 'agent'{' AND project=?' if project else ''}",
        sem_params,
    ).fetchall()
    trails = sorted(
        (
            {
                "id": r["id"],
                "label": _label(r),
                "trail": round(decay.decayed(r["trail"], r["trail_at"], _TRAIL_HALF_LIFE_DAYS), 2),
            }
            for r in trail_rows
        ),
        key=lambda t: t["trail"],
        reverse=True,
    )
    trails = [t for t in trails if t["trail"] >= 0.05][:12]

    pr = graph.pagerank(db, project=project)
    central_ids = sorted(pr, key=pr.get, reverse=True)[:8]
    central_rows = _hydrate(db, set(central_ids), project)
    central = [
        {"id": cid, "label": _label(central_rows[cid]), "score": pr[cid]}
        for cid in central_ids
        if cid in central_rows
    ]

    affinities = []
    for r in db.execute("SELECT agent_id, tag, weight, updated_at FROM task_affinity").fetchall():
        w = decay.decayed(r["weight"], r["updated_at"], affinity.HALF_LIFE_DAYS)
        if w >= affinity.MIN_WEIGHT:
            affinities.append({"agent_id": r["agent_id"], "tag": r["tag"], "weight": round(w, 3)})
    affinities.sort(key=lambda a: (a["agent_id"], -a["weight"]))

    conflict_rows = db.execute(
        f"SELECT id, content, headline, type, parents, created_at FROM memory"
        f" WHERE deleted_at IS NULL"
        f" AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value='sync-conflict')"
        f"{' AND project=?' if project else ''} ORDER BY created_at DESC LIMIT 20",
        sem_params,
    ).fetchall()
    conflicts = [
        {
            "id": r["id"],
            "label": _label(r),
            "parents": json.loads(r["parents"]),
            "created_at": r["created_at"],
        }
        for r in conflict_rows
    ]

    return {
        "graph": {"nodes": nodes, "edges": edges},
        "trails": trails,
        "central": central,
        "affinities": affinities,
        "conflicts": conflicts,
        "counts": {
            "nodes": len(nodes),
            "semantic_edges": sum(1 for e in edges if e["kind"] == "semantic"),
            "hebbian_edges": sum(1 for e in edges if e["kind"] == "hebbian"),
        },
    }
