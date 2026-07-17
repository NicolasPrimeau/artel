import json
import logging

from ..store import graph
from ..store.db import get_db
from .client import ArtelClient
from .llm import complete, is_configured

log = logging.getLogger(__name__)

_VALID = {"merge", "keep_winner", "keep_both"}

_SYSTEM = (
    "You resolve sync conflicts in a shared memory store for a fleet of AI agents. "
    "The same entry was edited concurrently on two mesh instances; replication kept a "
    "deterministic winner and preserved the losing write as a conflict sibling. "
    "Reconcile them semantically.\n"
    "Respond with ONE JSON object and nothing else:\n"
    '{"resolution": "merge", "content": "<one unified entry preserving what is true in both>"}\n'
    '{"resolution": "keep_winner"} — the sibling adds nothing, is stale, or is wrong\n'
    '{"resolution": "keep_both"} — they are genuinely distinct facts that should both exist\n'
    "Prefer merge when the versions complement each other. Never invent facts."
)


def _user_prompt(winner: dict, sib: dict) -> str:
    return (
        f"WINNER (kept by sync):\n{winner.get('content', '')}\n\n"
        f"SIBLING (concurrent edit that lost):\n{sib.get('content', '')}"
    )


def _parse_decision(text: str) -> dict | None:
    try:
        data = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("resolution") not in _VALID:
        return None
    if data["resolution"] == "merge" and not str(data.get("content") or "").strip():
        return None
    return data


async def _drop_sibling(client: ArtelClient, sib: dict) -> None:
    await client.delete_memory(sib["id"])
    db = get_db()
    with db:
        graph.remove_edges(db, sib["id"], graph.CONTRADICTS)


async def _keep_sibling(client: ArtelClient, sib: dict) -> None:
    tags = [t for t in (sib.get("tags") or []) if t != "sync-conflict"] + ["conflict-kept"]
    fields: dict = {"tags": tags}
    if sib.get("project"):
        fields["scope"] = "project"
    await client.patch_memory(sib["id"], **fields)


async def run_conflict_resolution(client: ArtelClient) -> None:
    if not is_configured():
        return
    siblings = await client.list_entries(tag="sync-conflict", limit=10)
    if not siblings:
        return
    resolved = {"merge": 0, "keep_winner": 0, "keep_both": 0, "orphaned": 0}
    for sib in siblings:
        parents = sib.get("parents") or []
        winner = None
        if parents:
            try:
                winner = await client.get_memory(parents[0])
            except Exception:
                winner = None
        if winner is None:
            try:
                await _drop_sibling(client, sib)
                resolved["orphaned"] += 1
            except Exception as e:
                log.warning("could not drop orphaned conflict sibling %s: %s", sib["id"], e)
            continue

        try:
            text = await complete(system=_SYSTEM, user=_user_prompt(winner, sib), max_tokens=1024)
        except Exception as e:
            log.warning("conflict resolution LLM call failed for %s: %s", sib["id"], e)
            continue
        decision = _parse_decision(text or "")
        if decision is None:
            log.warning("conflict resolution gave no valid decision for %s; leaving it", sib["id"])
            continue

        try:
            if decision["resolution"] == "merge":
                await client.patch_memory(winner["id"], content=str(decision["content"]))
                await _drop_sibling(client, sib)
            elif decision["resolution"] == "keep_winner":
                await _drop_sibling(client, sib)
            else:
                await _keep_sibling(client, sib)
            resolved[decision["resolution"]] += 1
        except Exception as e:
            log.warning("conflict resolution apply failed for %s: %s", sib["id"], e)

    total = sum(resolved.values())
    if total:
        await client.log(
            action="conflict_resolution",
            message=f"resolved {total} sync conflict{'s' if total != 1 else ''}",
            details=resolved,
        )
