import sqlite3
import uuid
from datetime import UTC, datetime

GROUNDS = "grounds"
RELIES_ON = "relies_on"
APPLIES_TO = "applies_to"
CONTRADICTS = "contradicts"
CORROBORATES = "corroborates"

RELS = (GROUNDS, RELIES_ON, APPLIES_TO, CONTRADICTS, CORROBORATES)
_INVALIDATING = (GROUNDS, RELIES_ON)
_SUPPORT_IN = (RELIES_ON, CORROBORATES, APPLIES_TO)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _nid() -> str:
    return str(uuid.uuid4())


def upsert_anchor(
    db: sqlite3.Connection,
    project: str | None,
    path: str,
    symbol: str,
    lang: str,
    start_line: int | None,
    end_line: int | None,
    sha: str,
    commit_sha: str | None = None,
) -> tuple[str, bool]:
    symbol = symbol or ""
    row = db.execute(
        "SELECT id, sha FROM code_anchor WHERE project IS ? AND path=? AND symbol=?",
        (project, path, symbol),
    ).fetchone()
    now = _now()
    if row is None:
        anchor_id = _nid()
        db.execute(
            "INSERT INTO code_anchor (id, project, path, symbol, lang, start_line, end_line, sha, commit_sha, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                anchor_id,
                project,
                path,
                symbol,
                lang or "",
                start_line,
                end_line,
                sha,
                commit_sha,
                now,
                now,
            ),
        )
        return anchor_id, True
    if row["sha"] != sha:
        db.execute(
            "UPDATE code_anchor SET sha=?, lang=?, start_line=?, end_line=?, commit_sha=?, updated_at=? WHERE id=?",
            (sha, lang or "", start_line, end_line, commit_sha, now, row["id"]),
        )
        return row["id"], True
    return row["id"], False


def get_anchor(db: sqlite3.Connection, anchor_id: str) -> dict | None:
    row = db.execute("SELECT * FROM code_anchor WHERE id=?", (anchor_id,)).fetchone()
    return dict(row) if row else None


def find_anchor(db: sqlite3.Connection, project: str | None, path: str, symbol: str) -> dict | None:
    row = db.execute(
        "SELECT * FROM code_anchor WHERE project IS ? AND path=? AND symbol=?",
        (project, path, symbol or ""),
    ).fetchone()
    return dict(row) if row else None


def add_edge(
    db: sqlite3.Connection,
    project: str | None,
    src: str,
    dst: str,
    rel: str,
    note: str = "",
) -> str:
    existing = db.execute(
        "SELECT id FROM memory_edge WHERE src=? AND dst=? AND rel=?", (src, dst, rel)
    ).fetchone()
    if existing:
        if note:
            db.execute("UPDATE memory_edge SET note=? WHERE id=?", (note, existing["id"]))
        return existing["id"]
    edge_id = _nid()
    db.execute(
        "INSERT INTO memory_edge (id, project, src, dst, rel, note, created_at) VALUES (?,?,?,?,?,?,?)",
        (edge_id, project, src, dst, rel, note, _now()),
    )
    return edge_id


def remove_edges(db: sqlite3.Connection, src: str, rel: str) -> None:
    db.execute("DELETE FROM memory_edge WHERE src=? AND rel=?", (src, rel))


def node_kind(db: sqlite3.Connection, node_id: str) -> str | None:
    if db.execute("SELECT 1 FROM code_anchor WHERE id=?", (node_id,)).fetchone():
        return "anchor"
    if db.execute("SELECT 1 FROM memory WHERE id=?", (node_id,)).fetchone():
        return "memory"
    return None


def edges_of(db: sqlite3.Connection, node_id: str) -> dict:
    out = [
        dict(r)
        for r in db.execute(
            "SELECT id, src, dst, rel, note FROM memory_edge WHERE src=? ORDER BY rel", (node_id,)
        ).fetchall()
    ]
    inc = [
        dict(r)
        for r in db.execute(
            "SELECT id, src, dst, rel, note FROM memory_edge WHERE dst=? ORDER BY rel", (node_id,)
        ).fetchall()
    ]
    return {"out": out, "in": inc}


def invalidate(db: sqlite3.Connection, changed_node_id: str) -> set[str]:
    now = _now()
    invalidated: set[str] = set()
    visited: set[str] = set()
    frontier = [changed_node_id]
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        deps = db.execute(
            "SELECT DISTINCT src FROM memory_edge WHERE dst=? AND rel IN (?,?)",
            (node, GROUNDS, RELIES_ON),
        ).fetchall()
        for d in deps:
            src = d["src"]
            mem = db.execute(
                "SELECT id, stale FROM memory WHERE id=? AND source_sha IS NOT NULL", (src,)
            ).fetchone()
            if mem is not None and not mem["stale"]:
                db.execute("UPDATE memory SET stale=1, updated_at=? WHERE id=?", (now, src))
                invalidated.add(src)
            frontier.append(src)
    return invalidated


def viability(db: sqlite3.Connection, node_id: str) -> dict:
    out = db.execute("SELECT rel, dst FROM memory_edge WHERE src=?", (node_id,)).fetchall()
    inc = db.execute("SELECT rel, src FROM memory_edge WHERE dst=?", (node_id,)).fetchall()

    mem = db.execute("SELECT source_sha, stale FROM memory WHERE id=?", (node_id,)).fetchone()
    source_sha = mem["source_sha"] if mem else None
    flagged_stale = bool(mem["stale"]) if mem else False

    fresh_grounds = 0
    stale_grounds = 0
    for e in out:
        if e["rel"] != GROUNDS:
            continue
        anchor = db.execute("SELECT sha FROM code_anchor WHERE id=?", (e["dst"],)).fetchone()
        if anchor is None:
            stale_grounds += 1
        elif flagged_stale or (source_sha is not None and anchor["sha"] != source_sha):
            stale_grounds += 1
        else:
            fresh_grounds += 1

    out_corroborates = sum(1 for e in out if e["rel"] == CORROBORATES)
    backlinks = sum(1 for e in inc if e["rel"] in _SUPPORT_IN)
    contradictions = sum(1 for e in out if e["rel"] == CONTRADICTS) + sum(
        1 for e in inc if e["rel"] == CONTRADICTS
    )

    raw = (
        1.0 * fresh_grounds
        + 0.5 * backlinks
        + 0.3 * out_corroborates
        - 1.0 * contradictions
        - 0.5 * stale_grounds
    )
    score = 0.0 if raw <= 0 else 1.0 - 2.0 ** (-raw)
    return {
        "score": round(score, 4),
        "degree": len(out) + len(inc),
        "fresh_grounds": fresh_grounds,
        "stale_grounds": stale_grounds,
        "backlinks": backlinks,
        "corroborates": out_corroborates,
        "contradictions": contradictions,
    }


# Per-relation conductance for spreading activation: supportive edges carry activation,
# contradiction inhibits it (carries negative). Mirrors the viability weighting.
_SPREAD_WEIGHTS = {
    RELIES_ON: 0.9,
    CORROBORATES: 0.8,
    APPLIES_TO: 0.7,
    GROUNDS: 0.6,
    CONTRADICTS: -0.7,
}


def spread_activation(
    db: sqlite3.Connection,
    seeds: list[str],
    *,
    decay: float = 0.5,
    hops: int = 3,
    min_activation: float = 0.05,
    limit: int = 20,
) -> list[tuple[str, float]]:
    """Spreading-activation retrieval over the memory graph (Anderson/Collins-Loftus).

    Each seed starts at 1.0; activation flows along edges scaled by per-relation
    conductance and a per-hop decay, accumulating at each node. Returns memory nodes
    (seeds excluded, net-positive only) ranked by activation — graph-native associative
    recall that surfaces what is *structurally* related, complementing vector/keyword search.
    """
    activation: dict[str, float] = {s: 1.0 for s in seeds}
    frontier: dict[str, float] = dict(activation)
    for _ in range(hops):
        nxt: dict[str, float] = {}
        for node, act in frontier.items():
            if abs(act) < min_activation:
                continue
            adj = edges_of(db, node)
            for e in adj["out"] + adj["in"]:
                weight = _SPREAD_WEIGHTS.get(e["rel"], 0.0)
                if weight == 0.0:
                    continue
                neighbor = e["dst"] if e["src"] == node else e["src"]
                flow = act * weight * decay
                if abs(flow) < min_activation:
                    continue
                activation[neighbor] = activation.get(neighbor, 0.0) + flow
                nxt[neighbor] = nxt.get(neighbor, 0.0) + flow
        frontier = nxt
        if not frontier:
            break
    seeds_set = set(seeds)
    ranked = [
        (nid, round(act, 4))
        for nid, act in activation.items()
        if nid not in seeds_set and act > 0 and node_kind(db, nid) == "memory"
    ]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:limit]
