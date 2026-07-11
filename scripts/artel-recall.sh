#!/usr/bin/env bash
# Artel UserPromptSubmit hook: surface memory + skills relevant to the prompt.
# Thin wrapper around _artel_hooks.py (config-gated + deduped there). Never blocks.
python3 "$(dirname "$0")/_artel_hooks.py" recall 2>/dev/null
exit 0
