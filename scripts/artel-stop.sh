#!/usr/bin/env bash
# Artel Stop hook: before the agent stops, deliver any unread inbox messages so a
# teammate reaching it mid-run lands at the natural stopping point instead of next
# session. Honors stop_hook_active to avoid a re-block loop. Gated on config;
# never errors (always exits 0). Read-only — does not mark messages read.

[ -z "${CLAUDE_PLUGIN_OPTION_ARTEL_URL:-}" ] && exit 0
[ -z "${CLAUDE_PLUGIN_OPTION_AGENT_ID:-}" ] && exit 0
[ -z "${CLAUDE_PLUGIN_OPTION_API_KEY:-}" ] && exit 0

python3 -c '
import json, os, sys, urllib.request

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
# do not re-block if we already blocked once for this stop — avoids an infinite loop
if payload.get("stop_hook_active"):
    sys.exit(0)

base = os.environ.get("CLAUDE_PLUGIN_OPTION_ARTEL_URL", "").rstrip("/")
req = urllib.request.Request(
    base + "/messages/inbox",
    headers={
        "x-agent-id": os.environ.get("CLAUDE_PLUGIN_OPTION_AGENT_ID", ""),
        "x-api-key": os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY", ""),
    },
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        msgs = json.load(resp)
except Exception:
    sys.exit(0)
if not isinstance(msgs, list) or not msgs:
    sys.exit(0)

lines = [str(m.get("from_agent", "?")) + ": " + str(m.get("body", "")) for m in msgs[:10]]
reason = (
    "[Artel] " + str(len(msgs)) + " unread message(s) arrived while you worked — "
    "handle or acknowledge them (mark read) before stopping:\n" + "\n".join(lines)
)
print(json.dumps({"decision": "block", "reason": reason}))
' 2>/dev/null || exit 0

exit 0
