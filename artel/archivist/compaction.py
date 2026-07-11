import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .client import ArtelClient
from .llm import complete, is_configured

log = logging.getLogger(__name__)

_BATCH = 20
_RELATED_LIMIT = 6

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
