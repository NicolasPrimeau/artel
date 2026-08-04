import json
import logging
from collections.abc import Awaitable, Callable

from .client import ArtelClient
from .llm import complete, is_configured

log = logging.getLogger(__name__)

BLUEPRINT_TAG = "blueprint"
_SKILL_TYPE = "skill"
_MAX_SKILLS = 10
_MAX_SKILL_CHARS = 20000

_SYSTEM = (
    "You are the Artel archivist compiling a long procedural skill into a BLUEPRINT: a "
    "directed acyclic graph of template tasks. The point is to move the procedure out of an "
    "agent's prompt (where it degrades — the agent gets sidetracked, stops early, forgets "
    "steps) and into a task graph the server expands on its own.\n\n"
    "Output ONLY a JSON object, no prose:\n"
    '{"name": "<short-kebab-case>", "description": "<one line>", '
    '"params": ["<name>", ...], "nodes": [<node>, ...]}\n\n'
    "A node is:\n"
    '{"id": "<short-kebab-case>", "title": "<imperative, one line>", '
    '"description": "<what the agent must actually do>", '
    '"expected_outcome": "<observable result>", "priority": "low|normal|high", '
    '"tags": ["..."], "deps": ["<node id>", ...], "foreach": "<node-id>.<path>", '
    '"completion_contract": {...}, "done_check": {...}}\n\n'
    "Rules you MUST follow:\n"
    "- Every node id is unique. Every entry in deps names a real node id. No cycles. "
    "At least one node has no deps (the root wave).\n"
    "- Use {param} to interpolate a declared param into title/description/expected_outcome. "
    "In a foreach node, use {item} for the whole item or {item.field} for one of its fields.\n"
    "- FAN-OUT is the highest-value part. When the skill says to do something for every "
    'discovered item ("one config per jurisdiction", "probe every candidate source"), model '
    'it as a foreach node: "foreach": "<upstream-node-id>.<path into that node\'s output>". '
    "The upstream node MUST be listed in this node's deps, and MUST declare a "
    "completion_contract producing that list — otherwise there is nothing to fan out over.\n"
    "- completion_contract is a JSON Schema subset: type (object/array/string/number/integer/"
    "boolean), required, properties, items, enum, minItems, minLength. Give one to any node "
    "whose output another node consumes. Omit it everywhere else.\n"
    '- done_check is optional and reads reality: {"kind": "payload", "path": "<field>", '
    '"min_items": <n>} checks the shape of the output. Use it on correctness-critical nodes. '
    "Omit it when there is nothing objective to check — do not invent checks.\n"
    "- Prefer few, meaty nodes over many trivial ones. A node is a unit of work an agent "
    "claims and finishes, not a single sentence of the original prose."
)

_REPAIR = (
    "The blueprint you produced was rejected by the validator. Fix EVERY problem listed and "
    "output the corrected JSON object only.\n\nProblems:\n"
)

# The compile step is the only judgment here; injecting it keeps the pass testable
# without a live LLM and swappable (Dependency Inversion), matching the capture
# compaction Extractor seam.
Compiler = Callable[[str, list[str]], Awaitable[dict]]


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        s = "\n".join(lines[1:end]).strip()
    return s


async def _compile_with_llm(skill_content: str, problems: list[str]) -> dict:
    user = f"Skill:\n{skill_content[:_MAX_SKILL_CHARS]}"
    if problems:
        user = _REPAIR + "\n".join(f"- {p}" for p in problems) + "\n\n" + user
    text = await complete(system=_SYSTEM, user=user, max_tokens=4096)
    return json.loads(_strip_fences(text))


def _problems_from(error: Exception) -> list[str]:
    detail: object = str(error)
    response = getattr(error, "response", None)
    if response is not None:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
    if isinstance(detail, dict):
        collected: list[str] = []
        for value in detail.values():
            if isinstance(value, list):
                collected.extend(str(v) for v in value)
            else:
                collected.append(str(value))
        return collected
    return [str(detail)]


def _needs_compile(entry: dict, blueprints: list[dict]) -> bool:
    for blueprint in blueprints:
        if blueprint.get("source_entry_id") == entry["id"]:
            return blueprint.get("source_version") != entry.get("version")
    return True


async def run_blueprint_compilation(
    client: ArtelClient, compiler: Compiler = _compile_with_llm
) -> int:
    if compiler is _compile_with_llm and not is_configured():
        return 0
    try:
        skills = await client.list_entries(type=_SKILL_TYPE, tag=BLUEPRINT_TAG, limit=_MAX_SKILLS)
    except Exception as e:
        log.error("blueprint compile: listing skills failed: %s", e)
        return 0
    if not skills:
        return 0
    try:
        blueprints = await client.list_blueprints()
    except Exception as e:
        log.error("blueprint compile: listing blueprints failed: %s", e)
        return 0

    compiled = 0
    for entry in skills:
        if not _needs_compile(entry, blueprints):
            continue
        problems: list[str] = []
        for attempt in range(2):
            try:
                document = await compiler(entry["content"], problems)
            except Exception as e:
                log.error("blueprint compile: model output unusable for %s: %s", entry["id"], e)
                break
            try:
                result = await client.create_blueprint(
                    document, source_entry_id=entry["id"], source_version=entry.get("version")
                )
            except Exception as e:
                problems = _problems_from(e)
                log.warning(
                    "blueprint compile: %s rejected (attempt %d): %s",
                    entry["id"],
                    attempt + 1,
                    "; ".join(problems)[:400],
                )
                continue
            compiled += 1
            log.info(
                "blueprint compile: %s -> [%s] v%s from skill %s",
                result.get("name"),
                result.get("id"),
                result.get("version"),
                entry["id"],
            )
            break
    return compiled
