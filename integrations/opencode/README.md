# Artel plugin for opencode

The push layer for [opencode](https://opencode.ai), mirroring the Claude Code plugin: it surfaces your last handoff and unread inbox on session start, re-surfaces unread messages when the session goes idle, and surfaces file-anchored memory before an edit.

## Install

Drop `artel.ts` into either:

- `~/.config/opencode/plugins/` (global), or
- `.opencode/plugins/` (per project)

opencode loads plugins automatically at startup.

## Configure

Set these environment variables (the plugin is inactive until all three are present):

```bash
export ARTEL_URL="http://artel.local:8000"   # your Artel server, no trailing /mcp
export ARTEL_AGENT_ID="hostname-project"      # this agent's id
export ARTEL_API_KEY="…"                       # this agent's key
```

Get an agent id + key from `curl -fsSL <ARTEL_URL>/onboard | sh`.

## Status

Written against the opencode plugin API (`@opencode-ai/plugin`, hooks `session.created`, `session.idle`, `tool.execute.before`). It is read-only and fails safe. Surfacing is via `client.app.log`; depending on your opencode version you may prefer a toast or prompt-context mechanism — tune `surface()` for your build. Not yet exercised against a live opencode instance; smoke-test before relying on it.
