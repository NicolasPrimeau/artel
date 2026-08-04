import re
from typing import Any

from pydantic import BaseModel, Field

FANOUT_ONCE = "once"
ITEM = "item"
RUN_TAG_PREFIX = "run"
CHECK_PAYLOAD = "payload"
_TYPE_ARRAY = "array"
_KEY_PROPERTIES = "properties"
_KEY_TYPE = "type"

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


class DoneCheck(BaseModel):
    kind: str
    path: str | None = None
    min_items: int | None = None
    query: str | None = None
    expect: str | None = None


class TemplateNode(BaseModel):
    id: str
    title: str
    description: str = ""
    expected_outcome: str = ""
    priority: str = "normal"
    tags: list[str] = []
    deps: list[str] = []
    foreach: str | None = None
    completion_contract: dict | None = None
    done_check: DoneCheck | None = None


class BlueprintDocument(BaseModel):
    name: str
    description: str = ""
    params: list[str] = []
    nodes: list[TemplateNode] = Field(min_length=1)


class BlueprintCreate(BaseModel):
    document: BlueprintDocument
    project: str | None = None
    source_entry_id: str | None = None
    source_version: int | None = None


class BlueprintInstantiate(BaseModel):
    params: dict = {}
    project: str | None = None


def run_tag(run_id: str, node_id: str) -> str:
    return f"{RUN_TAG_PREFIX}:{run_id}:{node_id}"


def parse_run_tag(tag: str) -> tuple[str, str] | None:
    parts = tag.split(":")
    if len(parts) != 3 or parts[0] != RUN_TAG_PREFIX:
        return None
    return parts[1], parts[2]


def find_run_tag(tags: list[str]) -> tuple[str, str] | None:
    for tag in tags:
        parsed = parse_run_tag(tag)
        if parsed:
            return parsed
    return None


def dig(value: Any, path: str) -> Any:
    if not path:
        return value
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def render(text: str, context: dict) -> str:
    def _sub(match: re.Match) -> str:
        resolved = dig(context, match.group(1))
        if resolved is None:
            return match.group(0)
        return str(resolved)

    return _PLACEHOLDER.sub(_sub, text)


def validate_document(doc: BlueprintDocument) -> list[str]:
    problems: list[str] = []
    ids = [n.id for n in doc.nodes]
    seen = set()
    for node_id in ids:
        if node_id in seen:
            problems.append(f"duplicate node id {node_id!r}")
        seen.add(node_id)
    known = set(ids)
    for node in doc.nodes:
        for dep in node.deps:
            if dep not in known:
                problems.append(f"{node.id}: unknown dependency {dep!r}")
        if node.foreach:
            source = node.foreach.split(".")[0]
            if source not in known:
                problems.append(f"{node.id}: foreach refers to unknown node {source!r}")
            elif source not in node.deps:
                problems.append(f"{node.id}: foreach over {source!r} requires it as a dependency")
        problems.extend(_check_problems(node))
    problems.extend(_cycles(doc))
    if not any(not n.deps for n in doc.nodes):
        problems.append("blueprint has no root node (every node has dependencies)")
    return problems


def contract_at(contract: dict, path: str) -> dict | None:
    node: dict | None = contract
    if not path:
        return node
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = (node.get(_KEY_PROPERTIES) or {}).get(part)
    return node if isinstance(node, dict) else None


def _check_problems(node: TemplateNode) -> list[str]:
    check = node.done_check
    if check is None or check.kind != CHECK_PAYLOAD:
        return []
    path = check.path or ""
    if node.completion_contract is None:
        return [
            f"{node.id}: a payload done-check needs a completion_contract on the same node — "
            "without one there is never an output to check and the node can never pass"
        ]
    target = contract_at(node.completion_contract, path)
    if target is None:
        return [f"{node.id}: done-check path {path!r} is not declared in the completion_contract"]
    if check.min_items is not None and target.get(_KEY_TYPE) != _TYPE_ARRAY:
        return [
            f"{node.id}: done-check min_items requires {path!r} to be an array in the "
            f"completion_contract, but it is declared as {target.get(_KEY_TYPE)!r}"
        ]
    return []


def _cycles(doc: BlueprintDocument) -> list[str]:
    deps = {n.id: [d for d in n.deps if d in {x.id for x in doc.nodes}] for n in doc.nodes}
    state: dict[str, int] = {}
    found: list[str] = []

    def visit(node_id: str) -> None:
        if state.get(node_id) == 2:
            return
        if state.get(node_id) == 1:
            found.append(f"dependency cycle through {node_id!r}")
            return
        state[node_id] = 1
        for dep in deps.get(node_id, []):
            visit(dep)
        state[node_id] = 2

    for node_id in deps:
        visit(node_id)
    return found


def roots(doc: BlueprintDocument) -> list[TemplateNode]:
    return [n for n in doc.nodes if not n.deps]


def successors(doc: BlueprintDocument, node_id: str) -> list[TemplateNode]:
    return [n for n in doc.nodes if node_id in n.deps]


def node_by_id(doc: BlueprintDocument, node_id: str) -> TemplateNode | None:
    for node in doc.nodes:
        if node.id == node_id:
            return node
    return None
