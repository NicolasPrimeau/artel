#!/usr/bin/env bash
# Artel statusline: a compact, cached indicator of board state (open tasks + unread
# messages). Not a plugin hook — wire it into Claude Code settings.json:
#
#   "statusLine": { "type": "command", "command": "/abs/path/to/artel-statusline.sh" }
#
# Result is cached ~10s so it never hammers the server. Config-gated; prints nothing
# when Artel is unset or unreachable.
python3 "$(dirname "$0")/_artel_hooks.py" status 2>/dev/null
exit 0
