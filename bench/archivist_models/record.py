import argparse
import asyncio
import inspect
import json
import os
from collections import Counter
from pathlib import Path

WORK = Path.home() / ".cache" / "artel-bakeoff"
QUOTA = {
    "_llm_ops_pass": 14,
    "_extract_with_llm": 16,
    "_refine_with_llm": 6,
    "run_headlines": 10,
    "run_conflict_resolution": 4,
    "_refresh_project_brief": 3,
    "_merge": 3,
}
NEUTRAL = {"_extract_with_llm": "{}", "_refine_with_llm": "{}"}


def _caller():
    for frame in inspect.stack()[2:8]:
        if frame.function in QUOTA:
            return frame.function
    return inspect.stack()[2].function


async def main(db_path, out, reopen, only):
    os.environ["DB_PATH"] = str(db_path)
    import httpx
    from httpx import ASGITransport

    import artel.archivist.compaction as compaction
    import artel.archivist.conflict as conflict
    import artel.archivist.conflicts as conflicts
    import artel.archivist.synthesis as synthesis
    from artel.archivist.client import ArtelClient
    from artel.archivist.config import settings as arch
    from artel.server.config import settings as srv
    from artel.store.db import get_db

    srv.db_path = str(db_path)
    db = get_db(str(db_path))
    with db:
        db.execute(
            "INSERT OR REPLACE INTO agents (id, api_key, role) VALUES ('archivist', 'bench', 'archivist')"
        )
        db.execute(
            "UPDATE memory SET headline_version = 0 WHERE id IN (SELECT id FROM memory "
            "WHERE type = 'doc' AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 12)"
        )
        if reopen:
            db.execute(
                "UPDATE captures SET digested_at = NULL, "
                "expires_at = strftime('%Y-%m-%dT%H:%M:%fZ','now','+1 day') "
                "WHERE id IN (SELECT id FROM captures ORDER BY created_at DESC LIMIT ?)",
                (reopen,),
            )
    arch.archivist_provider = "openrouter"
    arch.openrouter_api_key = "unused"
    arch.agent_keys = "archivist:bench"
    srv.archivist_agent_id = "archivist"

    recorded = []
    seen = Counter()

    async def record(system, user, max_tokens=2048, timeout=None):
        name = _caller()
        if seen[name] < QUOTA.get(name, 0):
            seen[name] += 1
            recorded.append(
                {"pass": name, "system": system, "user": user, "max_tokens": max_tokens}
            )
        return NEUTRAL.get(name, "")

    for mod in (synthesis, compaction, conflict, conflicts):
        mod.complete = record

    from artel.server.app import app

    client = ArtelClient()
    await client.aclose()
    client._http = httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://bench",
        headers={"x-agent-id": "archivist", "x-api-key": "bench"},
        timeout=120.0,
    )
    passes = {
        "compaction": compaction.run_capture_compaction,
        "conflicts": conflicts.run_conflict_resolution,
        "synthesis": lambda c: synthesis.run_synthesis(c, since_hours=168),
        "refinement": compaction.run_capture_refinement,
        "headlines": synthesis.run_headlines,
        "brief": synthesis.run_brief,
    }
    for name in only or passes:
        fn = passes[name]
        try:
            await fn(client)
        except Exception as e:
            print(f"{name} failed: {type(e).__name__}: {e}")
    await client.aclose()
    out.write_text(json.dumps(recorded, indent=1))
    print(json.dumps(dict(seen)), len(recorded), "prompts ->", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(WORK / "bake.db"))
    ap.add_argument("--out", default=str(WORK / "prompts.json"))
    ap.add_argument("--reopen-captures", type=int, default=20)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    asyncio.run(main(Path(a.db), Path(a.out), a.reopen_captures, a.only))
