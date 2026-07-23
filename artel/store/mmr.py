from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _normalize(relevance: dict[str, float], ids: list[str]) -> dict[str, float]:
    values = [relevance.get(i, 0.0) for i in ids]
    lo = min(values)
    hi = max(values)
    if hi - lo <= 0.0:
        return {i: 1.0 for i in ids}
    return {i: (relevance.get(i, 0.0) - lo) / (hi - lo) for i in ids}


def mmr_select(
    ids: list[str],
    relevance: dict[str, float],
    vectors: dict[str, list[float]],
    lambda_: float,
    k: int,
) -> list[str]:
    if k <= 0 or not ids:
        return []
    rel = _normalize(relevance, ids)
    selected: list[str] = []
    remaining = list(ids)
    while remaining and len(selected) < k:
        best_id = remaining[0]
        best_score = None
        for cid in remaining:
            redundancy = 0.0
            vc = vectors.get(cid)
            if vc is not None and selected:
                redundancy = max(
                    (cosine(vc, vectors[s]) for s in selected if vectors.get(s) is not None),
                    default=0.0,
                )
            score = lambda_ * rel[cid] - (1.0 - lambda_) * redundancy
            if best_score is None or score > best_score:
                best_score = score
                best_id = cid
        selected.append(best_id)
        remaining.remove(best_id)
    return selected
