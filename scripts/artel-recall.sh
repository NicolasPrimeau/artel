#!/usr/bin/env bash
# Artel UserPromptSubmit hook: surface memory + skills relevant to the prompt as
# additional context. Gated on config; never blocks a prompt (always exits 0).
# Read-only — GET /memory/search does not mutate anything.

[ -z "${CLAUDE_PLUGIN_OPTION_ARTEL_URL:-}" ] && exit 0
[ -z "${CLAUDE_PLUGIN_OPTION_AGENT_ID:-}" ] && exit 0
[ -z "${CLAUDE_PLUGIN_OPTION_API_KEY:-}" ] && exit 0

python3 -c '
import json, os, sys, urllib.parse, urllib.request

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
prompt = (payload.get("prompt") or "").strip()
if len(prompt) < 8:
    sys.exit(0)

base = os.environ.get("CLAUDE_PLUGIN_OPTION_ARTEL_URL", "").rstrip("/")
query = urllib.parse.urlencode(
    {"q": prompt[:300], "limit": "5", "confidence_min": "0.5", "max_content_length": "300"}
)
req = urllib.request.Request(
    base + "/memory/search?" + query,
    headers={
        "x-agent-id": os.environ.get("CLAUDE_PLUGIN_OPTION_AGENT_ID", ""),
        "x-api-key": os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY", ""),
    },
)
try:
    with urllib.request.urlopen(req, timeout=6) as resp:
        results = json.load(resp)
except Exception:
    sys.exit(0)
if not isinstance(results, list) or not results:
    sys.exit(0)

memories, skills = [], []
for entry in results:
    if not isinstance(entry, dict):
        continue
    (skills if entry.get("type") == "skill" else memories).append(entry)

def oneline(entry):
    return "- " + " ".join((entry.get("content") or "").split())[:160]

parts = []
if memories:
    parts.append("Relevant memory:\n" + "\n".join(oneline(e) for e in memories[:3]))
if skills:
    body = " ".join((skills[0].get("content") or "").split())[:160]
    parts.append("Skill that may apply: " + body)
if not parts:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "[Artel] " + "\n".join(parts),
    }
}))
' 2>/dev/null || exit 0

exit 0
