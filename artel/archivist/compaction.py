import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .client import ArtelClient
from .llm import complete, is_configured

log = logging.getLogger(__name__)

_BATCH = 20
_RELATED_LIMIT = 6

_PROVISIONAL_TAG = "capture-extracted"
_REFINE_WINDOW_HOURS = 6
_REFINE_MAX = 15

_SYSTEM = (
    "You are the Artel archivist compacting a raw agent session slice into project memory. "
    "Extract only durable, generalizable facts, decisions, or gotchas that will still be true "
    "in a week — never transient chatter, raw tool output, or one-off state. Prefer updating an "
    "existing related memory over creating a near-duplicate. Output a JSON object: "
    '{"facts": ["<new standalone memory>", ...], '
    '"updates": [{"id": "<existing memory id>", "content": "<merged content>"}, ...]}. '
    'If nothing is worth keeping, output {"facts": [], "updates": []}.'
)


@dataclass
class ExtractResult:
    facts: list[str] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)


# The extraction step is the only judgment in the pass; injecting it keeps the pass
# testable without a live LLM and swappable (Dependency Inversion).
Extractor = Callable[[str, list[dict]], Awaitable[ExtractResult]]


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        s = "\n".join(lines[1:end]).strip()
    return s


async def _extract_with_llm(content: str, related: list[dict]) -> ExtractResult:
    related_block = "\n\n".join(f"[{r['id']}] {r['content'][:300]}" for r in related)
    text = await complete(
        system=_SYSTEM,
        user=(
            (f"Existing related memory:\n{related_block}\n\n" if related_block else "")
            + f"Session slice:\n{content[:6000]}"
        ),
        max_tokens=1024,
    )
    try:
        data = json.loads(_strip_fences(text))
    except Exception:
        log.warning("capture extraction returned unparseable JSON")
        return ExtractResult()
    facts = [f for f in data.get("facts", []) if isinstance(f, str) and f.strip()]
    updates = [
        u
        for u in data.get("updates", [])
        if isinstance(u, dict) and u.get("id") and str(u.get("content", "")).strip()
    ]
    return ExtractResult(facts=facts, updates=updates)


async def _integrate(cap: dict, result: ExtractResult, valid_ids: set[str], client: ArtelClient):
    """Integrate one capture's candidates into memory — attribute new facts with session
    provenance, and apply updates only against memory the search actually returned."""
    written = updated = 0
    provenance = [
        "capture-extracted",
        "archivist-extracted",
        f"session:{cap.get('session_id') or 'unknown'}",
    ]
    for fact in result.facts:
        try:
            await client.write_memory(
                content=fact, type="memory", tags=provenance, project=cap.get("project")
            )
            written += 1
        except Exception as e:
            log.warning("could not write extracted fact: %s", e)
    for upd in result.updates:
        if upd["id"] not in valid_ids:
            continue
        try:
            await client.patch_memory(upd["id"], content=upd["content"])
            updated += 1
        except Exception as e:
            log.warning("could not update memory %s: %s", upd["id"], e)
    return written, updated


async def run_capture_compaction(client: ArtelClient, *, extract: Extractor = _extract_with_llm):
    """Minor compaction pass: drain the capture queue, extract durable memory, integrate it
    (reconcile via update-vs-create + provenance), then mark digested. Listing lazily prunes
    expired rows, so the queue stays bounded even in passive mode."""
    try:
        pending = await client.list_pending_captures(limit=_BATCH)
    except Exception as e:
        log.warning("could not list pending captures: %s", e)
        return
    # Passive mode: leave captures pending for a later LLM-configured run rather than
    # discarding them. Expired rows were already pruned by the list call above.
    if not pending or not is_configured():
        return

    digested: list[str] = []
    facts_written = updates_applied = 0
    for cap in pending:
        content = cap.get("content") or ""
        related = await client.search_memory(content[:400], limit=_RELATED_LIMIT)
        try:
            result = await extract(content, related)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # extraction failed — leave undigested so the next cycle retries it
            log.warning("capture extraction failed for %s: %s", (cap.get("id") or "")[:8], e)
            continue
        written, updated = await _integrate(cap, result, {r["id"] for r in related}, client)
        facts_written += written
        updates_applied += updated
        digested.append(cap["id"])

    if digested:
        try:
            await client.digest_captures(digested)
        except Exception as e:
            log.warning("could not mark captures digested: %s", e)
    await client.log(
        action="capture_compaction",
        message=(
            f"{len(digested)} capture(s) compacted: "
            f"{facts_written} fact(s) written, {updates_applied} updated"
        ),
        details={
            "digested": len(digested),
            "facts_written": facts_written,
            "updates_applied": updates_applied,
        },
    )


# --- major pass: consolidate / corroborate / promote provisional capture memory --------
# The minor pass lands captures as provisional L1 entries tagged `capture-extracted`.
# Because those are archivist-authored, the event-driven check_and_merge skips them, so
# near-duplicates accumulate. This major pass consolidates them (merge duplicates, raise
# confidence on corroboration, drop the provisional marker once stable), scoped to the
# recent capture delta so the cost is bounded — LSM major compaction, not a full-store scan.

_REFINE_SYSTEM = (
    "You are the Artel archivist consolidating provisional memories extracted from raw "
    "session captures. Merge duplicates and near-duplicates into one canonical entry, raise "
    "confidence when several independent captures corroborate the same fact, reconcile "
    "contradictions, and drop the provisional marker once an entry is stable. Only reference "
    'the ids given. Output JSON {"ops": [...]} where each op is one of: '
    '{"action": "consolidate", "keep": "<id>", "drop": ["<id>", ...], "content": "<merged>", '
    '"confidence": <0..1>, "tags": ["<tag>", ...]} to merge; '
    '{"action": "promote", "id": "<id>"} to keep a stable single entry (marker dropped); '
    '{"action": "discard", "id": "<id>"} to delete a non-durable one. '
    'If nothing needs doing, output {"ops": []}.'
)

RefineFn = Callable[[list[dict]], Awaitable[list[dict]]]


def _since(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _tags_of(entry: dict) -> list[str]:
    t = entry.get("tags")
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except Exception:
            t = []
    return t if isinstance(t, list) else []


def _clip(text, n):
    return " ".join(str(text or "").split())[:n]


async def _refine_with_llm(entries: list[dict]) -> list[dict]:
    listing = "\n".join(
        f"[{e['id']}] (conf {e.get('confidence', 1.0)}) {_clip(e.get('content'), 240)}"
        for e in entries
    )
    text = await complete(_REFINE_SYSTEM, "Provisional memories:\n" + listing, max_tokens=1500)
    try:
        data = json.loads(_strip_fences(text))
    except Exception:
        log.warning("capture refinement returned unparseable JSON")
        return []
    ops = data.get("ops")
    return ops if isinstance(ops, list) else []


async def _apply_refine_op(
    op: dict, provisional: dict[str, dict], client: ArtelClient
) -> str | None:
    """Apply one refinement op against the provisional set only; returns its kind or None."""
    action = op.get("action")
    if action == "consolidate" and op.get("keep") in provisional:
        fields: dict = {}
        if op.get("content"):
            fields["content"] = op["content"]
        if isinstance(op.get("confidence"), int | float):
            fields["confidence"] = float(op["confidence"])
        if isinstance(op.get("tags"), list):
            # setting tags also rewrites scope in the PATCH handler — pin it to project
            fields["tags"] = [t for t in op["tags"] if t != _PROVISIONAL_TAG]
            fields["scope"] = "project"
        if fields:
            await client.patch_memory(op["keep"], **fields)
        for drop in op.get("drop", []):
            if drop in provisional and drop != op["keep"]:
                await client.delete_memory(drop)
        return "consolidated"
    if action == "promote" and op.get("id") in provisional:
        kept = [t for t in _tags_of(provisional[op["id"]]) if t != _PROVISIONAL_TAG]
        await client.patch_memory(op["id"], tags=kept, scope="project")
        return "promoted"
    if action == "discard" and op.get("id") in provisional:
        await client.delete_memory(op["id"])
        return "discarded"
    return None


async def run_capture_refinement(client: ArtelClient, *, refine: RefineFn = _refine_with_llm):
    try:
        delta = await client.get_delta(_since(_REFINE_WINDOW_HOURS))
    except Exception as e:
        log.warning("could not fetch delta for capture refinement: %s", e)
        return
    provisional = {
        e["id"]: e for e in delta if e.get("type") == "memory" and _PROVISIONAL_TAG in _tags_of(e)
    }
    if len(provisional) < 2 or not is_configured():
        return
    try:
        ops = await refine(list(provisional.values())[:_REFINE_MAX])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("capture refinement failed: %s", e)
        return

    counts = {"consolidated": 0, "promoted": 0, "discarded": 0}
    for op in ops:
        if not isinstance(op, dict):
            continue
        try:
            kind = await _apply_refine_op(op, provisional, client)
        except Exception as e:
            log.warning("refine op %s failed: %s", op.get("action"), e)
            continue
        if kind:
            counts[kind] += 1
    await client.log(
        action="capture_refinement",
        message=(
            f"consolidated {counts['consolidated']}, promoted {counts['promoted']}, "
            f"discarded {counts['discarded']} provisional capture memories"
        ),
        details=counts,
    )
