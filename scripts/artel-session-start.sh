#!/usr/bin/env bash
# Artel SessionStart hook: a short nudge from the agent's last handoff. Thin wrapper
# around _artel_hooks.py (config-gated and size-budgeted there). Never blocks.
python3 "$(dirname "$0")/_artel_hooks.py" session 2>/dev/null
exit 0
