#!/usr/bin/env bash
# Artel PreToolUse hook (Edit/Write/MultiEdit/NotebookEdit): surface memory anchored
# to the file about to be modified. Thin wrapper around _artel_hooks.py (config-gated
# + deduped there). Never blocks a tool call.
python3 "$(dirname "$0")/_artel_hooks.py" gotcha 2>/dev/null
exit 0
