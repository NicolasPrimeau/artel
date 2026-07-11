#!/usr/bin/env bash
# Artel PreToolUse hook (Edit/Write/MultiEdit/NotebookEdit): surface shared memory
# anchored to the file about to be modified — gotchas, decisions, prior findings.
# Gated on config; never blocks a tool call (always exits 0). Read-only.

[ -z "${CLAUDE_PLUGIN_OPTION_ARTEL_URL:-}" ] && exit 0
[ -z "${CLAUDE_PLUGIN_OPTION_AGENT_ID:-}" ] && exit 0
[ -z "${CLAUDE_PLUGIN_OPTION_API_KEY:-}" ] && exit 0

python3 -c '
import json, os, sys, urllib.parse, urllib.request

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
tool_input = payload.get("tool_input") or {}
path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
name = os.path.basename(path)
if not name:
    sys.exit(0)
stem = os.path.splitext(name)[0]

base = os.environ.get("CLAUDE_PLUGIN_OPTION_ARTEL_URL", "").rstrip("/")
query = urllib.parse.urlencode(
    {"q": (name + " " + stem).strip(), "limit": "4", "confidence_min": "0.5", "max_content_length": "300"}
)
req = urllib.request.Request(
    base + "/memory/search?" + query,
    headers={
        "x-agent-id": os.environ.get("CLAUDE_PLUGIN_OPTION_AGENT_ID", ""),
        "x-api-key": os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY", ""),
    },
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        results = json.load(resp)
except Exception:
    sys.exit(0)

# only surface entries genuinely about this file — keeps it precise, not noisy.
# match the basename, the stem (files are often named by symbol, e.g. SCHEMA),
# or a compile-mode source_path anchor when the repo has been compiled.
def about_file(e):
    content = (e.get("content") or "").lower()
    if name.lower() in content:
        return True
    if len(stem) >= 4 and stem.lower() in content:
        return True
    source_path = (e.get("source_path") or "").lower()
    return source_path.endswith("/" + name.lower()) or source_path == name.lower()

hits = [e for e in results if isinstance(e, dict) and about_file(e)]
if not hits:
    sys.exit(0)

lines = ["- " + " ".join((e.get("content") or "").split())[:180] for e in hits[:2]]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "[Artel] Notes on " + name + " from shared memory:\n" + "\n".join(lines),
    }
}))
' 2>/dev/null || exit 0

exit 0
