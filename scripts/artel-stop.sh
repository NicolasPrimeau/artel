#!/usr/bin/env bash
# Artel Stop hook: deliver unread inbox messages at the stopping point so a teammate
# reaching the agent mid-run lands now, not next session. Thin wrapper around
# _artel_hooks.py (honors stop_hook_active + dedup there). Never errors.
python3 "$(dirname "$0")/_artel_hooks.py" stop 2>/dev/null
exit 0
