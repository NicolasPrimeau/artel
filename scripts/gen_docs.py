#!/usr/bin/env python3
"""Generate the reference half of the docs site from the code itself.

The README rotted because it hand-duplicated things that already have a
machine-readable source. Everything here is derived: MCP tools from their
docstrings (which MCP already shows to agents, so they are the best-maintained
prose in the repo), REST from openapi.json, configuration from the Settings
classes. Nothing in the generated pages is typed by hand, so nothing in them
can drift.

Run: uv run python scripts/gen_docs.py
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "docs" / "reference"

MCP_SOURCE = ROOT / "artel" / "mcp" / "server.py"
OPENAPI = ROOT / "openapi.json"

SETTINGS_SOURCES = [
    ("Server", ROOT / "artel" / "server" / "config.py", "Settings"),
    ("MCP adapter", ROOT / "artel" / "mcp" / "config.py", "MCPSettings"),
    ("Archivist", ROOT / "artel" / "archivist" / "config.py", "ArchivistSettings"),
]

# Descriptions cannot be derived — pydantic fields carry no help text here. The
# generator emits every field it finds regardless, so a new setting can never be
# silently missing from the docs; it shows up undocumented and loud instead.
SETTING_NOTES: dict[str, str] = {
    "agent_keys": "`agent-id:api-key` pairs, comma-separated. An optional `:proj1;proj2` suffix scopes an agent to projects.",
    "db_path": "SQLite database path.",
    "host": "Bind address.",
    "port": "HTTP port for the REST API, UI, and MCP endpoint.",
    "reload": "Auto-reload on code change (development only).",
    "registration_key": "Shared key required by `/agents/self-register`. Unset disables open registration.",
    "ui_password": "Password for the dashboard.",
    "ui_agent_id": "Agent identity the dashboard acts as.",
    "ui_default_theme": "Default dashboard theme.",
    "viewer_agent_id": "Read-only agent used by public sandbox deployments.",
    "demo_mode": "Relaxes limits for a public demo instance.",
    "archivist_agent_id": "Agent id the archivist authenticates as.",
    "public_url": "Externally reachable base URL, used in OAuth metadata and onboarding.",
    "mcp_url": "Override for the MCP URL advertised to clients.",
    "jwt_ttl": "OAuth token lifetime, in seconds.",
    "mdns_enabled": "Announce this instance on the LAN via mDNS.",
    "gossip_enabled": "Exchange peer lists with meshed instances.",
    "recall_bandit_enabled": "Enable the contextual bandit for recall ranking. Off means observe-only.",
    "recall_reinforce_gain": "How strongly a retrieval reinforces an entry's confidence.",
    "regret_threshold": "Confidence below which reading an entry is recorded as a decay-regret event — the decay controller's sensor.",
    "artel_url": "Base URL of the Artel server to connect to.",
    "mcp_agent_id": "Agent identity for the MCP adapter.",
    "mcp_agent_key": "API key for the MCP adapter.",
    "mcp_registration_key": "Registration key used when self-registering.",
    "mcp_transport": "`stdio` or `sse`.",
    "mcp_host": "Bind address for the standalone MCP server.",
    "mcp_port": "Port for the standalone MCP server.",
    "mcp_project": "Default project for memory and task calls.",
    "archivist_id": "Agent id the archivist runs as.",
    "anthropic_api_key": "Anthropic API key for archivist reasoning.",
    "openrouter_api_key": "OpenRouter API key for archivist reasoning.",
    "archivist_provider": "`anthropic`, `openrouter`, `openai`, or `claude-sdk`.",
    "archivist_model": "Model override. Empty uses the provider default. OpenRouter needs a vendor-prefixed slug.",
    "archivist_api_key": "Overrides the provider-specific key.",
    "archivist_base_url": "Custom base URL for OpenAI-compatible providers. Defaults to OpenRouter's when the provider is `openrouter`.",
    "synthesis_interval": "Seconds between archivist cycles.",
    "lease_ttl_seconds": "Curator lease lifetime. Only the lease holder runs passes.",
    "lease_renew_seconds": "How often the lease is renewed.",
    "conflict_threshold": "Similarity above which two memories are treated as conflicting.",
    "directive_conflict_threshold": "Similarity threshold for conflicting directives.",
    "decay_rate": "Per-cycle confidence multiplier for unread memory.",
    "decay_floor": "Confidence below which an entry is eligible for deletion.",
    "decay_window_days": "Age before decay begins to apply.",
    "promotion_memory_min_version": "Edits required before a memory can be promoted to a doc.",
    "promotion_stability_days": "How long an entry must be stable before promotion.",
    "promotion_min_confidence": "Minimum confidence for promotion.",
    "promotion_distinct_readers": "Distinct readers required before promotion.",
    "control_decay_enabled": "Enable the PI controller that tunes the decay rate.",
    "control_decay_kp": "Proportional gain.",
    "control_decay_ki": "Integral gain.",
    "control_decay_regret_setpoint": "Target regret the controller drives toward.",
    "control_decay_min": "Lower bound on the controlled decay rate.",
    "control_decay_max": "Upper bound on the controlled decay rate.",
    "control_decay_deadband": "Error band inside which the controller does nothing.",
    "control_decay_leak": "Integral leak, preventing windup.",
}

PREAMBLE = "<!-- Generated by scripts/gen_docs.py — do not edit by hand. -->\n\n"


def _tool_defs(path: Path) -> list[dict]:
    tree = ast.parse(path.read_text())
    tools = []
    for node in tree.body:
        # Most tools are async, but a couple are plain defs — match both or they
        # silently vanish from the reference.
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        decorated = any(
            (isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "tool")
            or getattr(d, "attr", None) == "tool"
            for d in node.decorator_list
        )
        if not decorated:
            continue
        doc = ast.get_docstring(node) or ""
        read_only = "readOnlyHint=True" in ast.unparse(node.decorator_list[0])
        tools.append(
            {
                "name": node.name,
                "signature": _signature(node),
                "doc": doc,
                "read_only": read_only,
            }
        )
    return tools


def _signature(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str:
    args = []
    defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
    for arg, default in zip(node.args.args, defaults, strict=True):
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            piece += f" = {ast.unparse(default)}"
        args.append(piece)
    return f"{node.name}({', '.join(args)})"


def _split_doc(doc: str) -> tuple[str, str]:
    """Separate the narrative from the Args: block so parameters can be a table."""
    if "\nArgs:" not in doc:
        return doc.strip(), ""
    body, _, args = doc.partition("\nArgs:")
    return body.strip(), args.strip()


def _args_table(args_block: str) -> str:
    if not args_block:
        return ""
    rows = []
    current: list[str] = []
    for line in args_block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped and not stripped.startswith(" ") and len(line) - len(line.lstrip()) <= 4:
            if current:
                rows.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        rows.append(" ".join(current))
    lines = ["| Parameter | Description |", "| --- | --- |"]
    for row in rows:
        name, _, desc = row.partition(":")
        # Escaped outside the f-string: a backslash in an f-string expression is a
        # syntax error before Python 3.12, and CI tests 3.11.
        cell = desc.strip().replace("|", "\\|")
        lines.append(f"| `{name.strip()}` | {cell} |")
    return "\n".join(lines)


def write_mcp_reference() -> int:
    tools = _tool_defs(MCP_SOURCE)
    out = [PREAMBLE, "# MCP tools\n"]
    out.append(
        "Every tool an agent can call over MCP. These descriptions are the same text "
        "the protocol shows to a connected agent, generated from the source.\n"
    )
    out.append(f"\n**{len(tools)} tools.** Read-only tools are marked.\n")
    for tool in sorted(tools, key=lambda t: t["name"]):
        body, args = _split_doc(tool["doc"])
        badge = " *(read-only)*" if tool["read_only"] else ""
        out.append(f"\n## `{tool['name']}`{badge}\n")
        out.append(f"```python\n{tool['signature']}\n```\n")
        if body:
            out.append(f"\n{body}\n")
        table = _args_table(args)
        if table:
            out.append(f"\n{table}\n")
    (REFERENCE / "mcp-tools.md").write_text("".join(out))
    return len(tools)


def write_rest_reference() -> int:
    spec = json.loads(OPENAPI.read_text())
    by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            tag = (op.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))

    out = [PREAMBLE, "# REST API\n"]
    out.append(
        "\nEvery endpoint, generated from `openapi.json`. All requests require "
        "`X-Agent-ID` and `X-API-Key` headers except `/agents/self-register` and `/onboard`.\n"
    )
    out.append(
        "\nA running server also serves interactive docs at `/docs` and the raw schema "
        "at `/openapi.json`.\n"
    )
    total = 0
    for tag in sorted(by_tag):
        out.append(f"\n## {tag}\n")
        for method, path, op in sorted(by_tag[tag], key=lambda r: (r[1], r[0])):
            total += 1
            summary = op.get("summary") or ""
            out.append(f"\n### `{method} {path}`\n")
            if summary:
                out.append(f"\n{summary}\n")
            params = op.get("parameters") or []
            if params:
                out.append(
                    "\n| Parameter | In | Required | Description |\n| --- | --- | --- | --- |\n"
                )
                for p in params:
                    req = "yes" if p.get("required") else "no"
                    desc = (p.get("description") or "").replace("|", "\\|")
                    out.append(f"| `{p.get('name')}` | {p.get('in')} | {req} | {desc} |\n")
    (REFERENCE / "rest.md").write_text("".join(out))
    return total


def _settings_fields(path: Path, class_name: str) -> list[tuple[str, str, str]]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    name = item.target.id
                    if name.startswith("_") or name == "model_config":
                        continue
                    annotation = ast.unparse(item.annotation)
                    default = ast.unparse(item.value) if item.value is not None else ""
                    fields.append((name, annotation, default))
            return fields
    return []


def write_config_reference() -> tuple[int, list[str]]:
    out = [PREAMBLE, "# Configuration\n"]
    out.append(
        "\nEvery setting, generated from the `BaseSettings` classes. Set them as "
        "environment variables (upper-case) or in a `.env` file.\n"
    )
    total = 0
    undocumented: list[str] = []
    for label, path, class_name in SETTINGS_SOURCES:
        fields = _settings_fields(path, class_name)
        if not fields:
            continue
        out.append(f"\n## {label}\n")
        out.append("\n| Variable | Type | Default | Description |\n| --- | --- | --- | --- |\n")
        for name, annotation, default in fields:
            total += 1
            note = SETTING_NOTES.get(name)
            if note is None:
                undocumented.append(name)
                note = "**Undocumented — add it to `SETTING_NOTES` in `scripts/gen_docs.py`.**"
            shown = f"`{default}`" if default not in ("", "''", '""') else "—"
            out.append(f"| `{name.upper()}` | `{annotation}` | {shown} | {note} |\n")
    (REFERENCE / "configuration.md").write_text("".join(out))
    return total, undocumented


def main() -> int:
    REFERENCE.mkdir(parents=True, exist_ok=True)
    if not OPENAPI.exists():
        print("openapi.json is missing; run scripts/gen_openapi.py first", file=sys.stderr)
        return 1
    tools = write_mcp_reference()
    routes = write_rest_reference()
    settings, undocumented = write_config_reference()
    print(f"mcp-tools.md      {tools} tools")
    print(f"rest.md           {routes} endpoints")
    print(f"configuration.md  {settings} settings")
    if undocumented:
        # Loud, but not fatal: a new setting reaching the docs undocumented is far
        # better than it never reaching them at all.
        print(f"\nWARNING: {len(undocumented)} setting(s) have no description:", file=sys.stderr)
        for name in undocumented:
            print(f"  - {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
