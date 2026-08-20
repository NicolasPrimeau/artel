# The Claude Code plugin

A notepad you have to remember to open is a notepad you stop opening. On its own, Artel is **pull**: tools you or an agent call when you think to — and you won't always think to.

The plugin adds the **push** half. It volunteers the right note at the right moment and writes down what happens, so the pad's value stops depending on anyone's discipline.

**Install — one line, no prompts:**

```bash
curl -fsSL http://<host>:8000/plugin/install | sh
```

Your own instance serves that script — point it at the Artel you started above.

This registers an agent, writes `ARTEL_URL` / `ARTEL_AGENT_ID` / `ARTEL_API_KEY` to `~/.config/artel/env.sh` (sourced from your shell profile), and installs the plugin via the `claude` CLI. It's a plain shell script, so an agent can run it for you. Then start a new Claude Code session.

Prefer to do it by hand? Set those three env vars, then in Claude Code:

```
/plugin marketplace add NicolasPrimeau/artel
/plugin install artel@artel
```

The plugin's MCP server and hooks read `${ARTEL_*}` from the environment — there's no interactive config step.

Every hook is config-gated, fail-safe (a missing or down server is harmless), tightly ranked (a few high-confidence results, deduped per session so nothing re-injects), and — where it matters — entirely off the agent's hot path.

| When | What the plugin does |
|------|----------------------|
| **Session start** | injects your last handoff and what changed in memory while you were gone |
| **Every prompt** | surfaces the most relevant memories and a matching skill, plus any new inbox messages |
| **Before an edit** | shows memory anchored to *that file* — gotchas, decisions, prior findings — before you touch it |
| **Before it stops** | delivers unread messages, so a teammate reaching you mid-run lands now, not next session |
| **Every turn · before compaction** | captures the session slice for the archivist ([Capture](capture.md)) — a ~10 ms local spool, never a network call on the hot path |

**Slash commands:** `/artel-recall` (search shared memory), `/artel-remember` (write a fact), `/artel-handoff` (save a handoff), `/artel-tasks` (show or claim the next task).

**Optional statusline** — open task and unread-message counts, cached, in your prompt. Add to `settings.json`:

```json
"statusLine": { "type": "command", "command": "/path/to/artel/scripts/artel-statusline.sh" }
```

Not seeing anything? Run `scripts/artel-doctor.sh` to check config and connectivity (it never prints your key).
