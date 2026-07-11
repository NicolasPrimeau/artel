#!/usr/bin/env bash
# Artel UserPromptSubmit hook: surface new unread inbox messages as context. Thin
# wrapper around _artel_hooks.py — deduped per session so the same message is not
# re-injected on every prompt. Read-only; never blocks a prompt.
python3 "$(dirname "$0")/_artel_hooks.py" inbox 2>/dev/null
exit 0
