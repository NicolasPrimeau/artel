import argparse
import asyncio
import json
import math
import os
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK_CONFIDENCE_MIN = 0.1
STOP = set(
    "a an and are as at be but by can could do does for from get go got has have how i if in "
    "into is it its just let like me my no not now of ok on or our so that the then there "
    "these they this to up us was we what when where which who why will with would yes you "
    "your also any anything else again all been did done make more much need should some "
    "still sure think want way well".split()
)


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


def open_copy(src):
    workdir = Path.home() / ".cache" / "artel-recall-eval" / "run"
    workdir.mkdir(parents=True, exist_ok=True)
    tmp = workdir / "eval.db"
    shutil.copy(src, tmp)
    for suffix in ("-wal", "-shm"):
        if Path(str(src) + suffix).exists():
            shutil.copy(str(src) + suffix, str(tmp) + suffix)
    return tmp


async def retrieve(case, limit, max_distance=None):
    from artel.server.config import settings
    from artel.server.routes import memory as mem
    from artel.store.db import get_db
    from artel.store.embeddings import embed

    settings.archivist_agent_id = case["agent"]
    results = await mem.search_memory(
        q=case["prompt"][:300],
        limit=limit,
        project=case["project"],
        tag=None,
        type=None,
        agent=None,
        max_distance=max_distance,
        confidence_min=HOOK_CONFIDENCE_MIN,
        max_content_length=None,
        diversify=True,
        context="recall",
        agent_id=case["agent"],
    )
    qv = embed(case["prompt"][:300])
    vecs = mem._fetch_vectors(get_db(), [r.id for r in results])
    qw = words(case["prompt"])
    return [
        {
            "id": r.id,
            "type": r.type,
            "project": r.project,
            "confidence": round(r.confidence, 2),
            "sim": round(cosine(qv, vecs[r.id]), 3) if qv and r.id in vecs else 0.0,
            "overlap": len(qw & words(r.content)),
            "content": r.content,
        }
        for r in results
    ]


def injected(hits, policy):
    memories = [h for h in hits if h["type"] != "skill"]
    skills = [h for h in hits if h["type"] == "skill"]
    return policy(memories)[:2] + policy(skills)[:1]


POLICIES = {
    "current": lambda hs: hs,
    "sim>=0.30": lambda hs: [h for h in hs if h["sim"] >= 0.30],
    "sim>=0.35": lambda hs: [h for h in hs if h["sim"] >= 0.35],
    "sim>=0.40": lambda hs: [h for h in hs if h["sim"] >= 0.40],
    "sim>=0.45": lambda hs: [h for h in hs if h["sim"] >= 0.45],
    "sim>=0.50": lambda hs: [h for h in hs if h["sim"] >= 0.50],
    "overlap>=2": lambda hs: [h for h in hs if h["overlap"] >= 2],
    "overlap>=3": lambda hs: [h for h in hs if h["overlap"] >= 3],
    "sim>=0.40|overlap>=3": lambda hs: [h for h in hs if h["sim"] >= 0.40 or h["overlap"] >= 3],
    "sim>=0.35&overlap>=2": lambda hs: [h for h in hs if h["sim"] >= 0.35 and h["overlap"] >= 2],
    "top1-only": lambda hs: hs[:1],
}


def score(cases, runs, policy):
    shown = hits = none_cases = none_fired = pos_cases = pos_found = 0
    for case, hs in zip(cases, runs):
        inj = injected(hs, policy)
        relevant = set(case["relevant"])
        shown += len(inj)
        hits += sum(1 for h in inj if h["id"] in relevant)
        if relevant:
            pos_cases += 1
            pos_found += any(h["id"] in relevant for h in inj)
        else:
            none_cases += 1
            none_fired += bool(inj)
    return {
        "injected": shown,
        "precision": round(hits / shown, 2) if shown else None,
        "useful_per_prompt": round(hits / len(cases), 2),
        "positives_served": f"{pos_found}/{pos_cases}",
        "noise_on_none": f"{none_fired}/{none_cases}",
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--labels", default=str(HERE / "labels.json"))
    ap.add_argument("--pool", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args()

    os.environ["DB_PATH"] = str(open_copy(args.db))
    cases = json.loads(Path(args.labels).read_text())

    if args.pool:
        pool = []
        for case in cases:
            hs = await retrieve(case, args.pool)
            pool.append({**case, "candidates": hs})
        Path(args.out).write_text(json.dumps(pool, indent=1))
        return

    runs = [await retrieve(case, 6) for case in cases]
    for name, policy in POLICIES.items():
        print(f"{name:24} {json.dumps(score(cases, runs, policy))}")
    for cos in (0.30, 0.35, 0.40):
        dist = round(math.sqrt(2 - 2 * cos), 3)
        server = [await retrieve(case, 6, dist) for case in cases]
        label = f"max_distance={dist}"
        print(f"{label:24} {json.dumps(score(cases, server, POLICIES['current']))}")


if __name__ == "__main__":
    asyncio.run(main())
