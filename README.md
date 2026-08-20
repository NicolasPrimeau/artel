# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![Glama](https://glama.ai/mcp/servers/NicolasPrimeau/artel/badges/score.svg)](https://glama.ai/mcp/servers/NicolasPrimeau/artel)
[![smithery badge](https://smithery.ai/badge/nicolas-primeau/artel)](https://smithery.ai/servers/nicolas-primeau/artel)
[![Docs](https://img.shields.io/badge/docs-artel-teal)](https://artel.run)

**A smart notepad for everything you figure out — one that learns.**

Write it down once. Artel hands it back at the moment it matters: the gotcha about *this* file right before you edit it, what you decided last Tuesday when you sit back down today, the thing someone else already learned the hard way. Nothing to file, nothing to tag, nothing to remember to go look up.

A normal notepad waits to be opened. This one speaks up.

And it doesn't just sit there accumulating. A background archivist reads what piles up — merging notes that say the same thing, connecting findings you never thought to link, letting stale things fade, promoting what keeps proving true. The notepad gets sharper the more you use it.

Your AI agents write in it too. Every coding session leaves behind what it learned, so the pad fills itself while you work — and what one session discovers, the next one already knows.

**It's yours.** You run it, on your own machine. Your ideas don't go to anyone's cloud.

## What that looks like

| You're about to… | Artel says |
|---|---|
| edit `auth.py` | "last time: the token refresh silently no-ops when the clock skews" |
| start work Monday | "Friday you were mid-way through the migration; here's where you stopped" |
| debug a flaky test | "you hit this in March — it was the shared fixture, not the test" |
| ask a question | the three notes that answer it, before you finish typing |

You never opened a file to find any of that.

---

## Quick start

There is no public instance to point at — this is your notepad, so you run it. One container, one port:

```bash
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/.env.example
cp .env.example .env
# edit .env: set UI_PASSWORD, and a key for the archivist if you want one
# (ANTHROPIC_API_KEY, or OPENROUTER_API_KEY with ARCHIVIST_PROVIDER=openrouter)
docker compose up -d
```

API + UI at `http://<host>:8000`, MCP at `http://<host>:8000/mcp`. Images at `ghcr.io/nicolasprimeau/artel:edge`.

Once running, register an agent:

```bash
curl -fsSL http://<host>:8000/onboard | sh
```

> **mDNS note:** the `mdns` service uses `network_mode: host` and only works on Linux. Remove it on Mac/Windows Docker Desktop.

---

## Under the hood

A server, a database, and a librarian. Notes go in over HTTP or MCP; embeddings make them findable by meaning rather than keyword; the archivist works the pile while you're not looking.

```
  you · Claude Code · opencode · Claude API · AutoGen
        │   push: notes/skills/gotchas in  ┄  capture: sessions out
        ▼
   REST / MCP ──► Artel Server ──► SQLite (WAL) + embeddings
                     ├── notes — semantic search · confidence decay · knowledge graph
                     ├── captures queue ──► archivist compaction ──► notes
                     ├── tasks · messages · events · session handoffs
                     └── archivist — capture · synthesis · merge · decay · promote
        │
   mesh (CRDT feeds + mDNS) ◄──► your other machines
```

---

## Table of contents

- [Features](#features)
- [The Claude Code plugin — the part that speaks up](#the-claude-code-plugin--the-part-that-speaks-up)
- [Capture](#capture)
- [Mesh](#mesh)
- [Compile mode](#compile-mode)
- [Archivist](#archivist)
- [Dashboard](#dashboard)
- [Memory](#memory)
- [Claude Code (MCP)](#claude-code-mcp)
- [OpenCode (MCP)](#opencode-mcp)
- [ACP editors (Zed and friends)](#acp-editors-zed-and-friends)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Development](#development)

---

## Features

Grouped by what they do for you, not by what they are.

**Writing things down**

- **Just write** — no folders, no tags, no filing. Notes are found by meaning, not by remembering the words you used.
- **Five kinds of note, different lifespans** — `memory` (the default; fades if it stops being true), `doc` (settled reference, promoted by the archivist), `directive` (a standing instruction that never fades), `skill` (how to do a thing), `compiled` (pinned to a piece of source code — it re-derives when the code changes instead of quietly going stale).
- **Capture** — sessions are spooled to disk in ~10 ms and turned into clean notes later, so writing never slows you down and a burst of raw noise can't degrade the pad.

**Getting it back without asking**

- **It speaks up** — the Claude Code plugin surfaces the right note at the right moment: on session start, on each prompt, and — the good one — file-anchored notes right before you edit that file.
- **Session handoffs** — stop mid-thought, pick up with full context tomorrow, on another machine, or in another agent entirely.
- **Dashboard** — a UI for when you do want to browse, search, and read the pad directly.

**The part that learns**

- **Archivist** — a background agent that merges duplicates, synthesizes higher-level findings out of scattered notes, resolves contradictions, lets unused knowledge decay, and promotes what proves stable. This is the difference between a pile of notes and something that gets better.
- **Knowledge graph** — notes link to related notes, so one answer pulls its neighbours along.
- **Feed subscriptions** — point it at an RSS or Atom feed and new items land in the pad on their own.

**More than one of you**

- **Agents share the pad** — anything that speaks HTTP or MCP reads and writes the same notes, so what one session learns, the rest already know.
- **Tasks and messages** — hand work between agents, leave each other notes, no central scheduler.
- **Mesh** — run it on several machines and they converge as CRDTs, with LAN peers found over mDNS. No coordinator, no cloud.

---

## The Claude Code plugin — the part that speaks up

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
| **Every turn · before compaction** | captures the session slice for the archivist ([Capture](#capture)) — a ~10 ms local spool, never a network call on the hot path |

**Slash commands:** `/artel-recall` (search shared memory), `/artel-remember` (write a fact), `/artel-handoff` (save a handoff), `/artel-tasks` (show or claim the next task).

**Optional statusline** — open task and unread-message counts, cached, in your prompt. Add to `settings.json`:

```json
"statusLine": { "type": "command", "command": "/path/to/artel/scripts/artel-statusline.sh" }
```

Not seeing anything? Run `scripts/artel-doctor.sh` to check config and connectivity (it never prints your key).

---

## Capture

The plugin surfaces memory *in*. Capture is the other direction — turning what happens in a session into durable memory *out* — without slowing the agent and without letting raw noise pollute the store.

**A two-tier write.** Agents don't reliably write memories back, and pouring a high-pace firehose straight into `memory` would cost an embedding per raw slice and pollute both search and the mesh. So capture lands in a separate **ingest queue** (`captures`) that is deliberately *not* embedded, *not* full-text indexed, *not* replicated over the mesh, and *not* returned by search. Memory is protected structurally: **the archivist is the only path from the queue into memory.**

**Off the hot path.** The `Stop` and `PreCompact` hooks do one thing — append the session payload to a local spool file and fork a detached drainer, then exit (~10 ms, no parsing, no network). The detached drainer compresses each session's new transcript slice (keeps the reasoning, drops bulky tool output), then ships it to the queue. The spool is a durable write-ahead log: if a drainer dies, the next hook's drainer picks up where it left off. Triggers are `Stop` (throttled by a per-session cursor and a size floor) and `PreCompact` (a forced flush right before context is evicted) — **never `SessionEnd`**, because agent sessions rarely end cleanly.

**Leveled compaction (LSM-style).** The archivist drains the queue and integrates each slice into memory — extracting durable facts, reconciling against what already exists (update rather than duplicate), and attaching session provenance. A second, less frequent pass consolidates the provisional entries: merging duplicates, raising confidence when independent sessions corroborate the same fact, reconciling contradictions, and promoting stable knowledge — scoped to the recent delta so the cost stays bounded. Raw captures → provisional memory → consolidated, canonical memory, refined over time.

The net effect: memory quality is decoupled from write volume. Writing fast only fills the queue; only the archivist's judgment turns a capture into memory.

---

## Mesh

Each instance publishes memory as Atom and JSON Feed. Link two instances and memory replicates as a CRDT — keyed by immutable id, idempotent on ingest, no central coordinator. LAN peers discover each other via mDNS (`_artel._tcp.local.`) and link with one click. Each instance's archivist only synthesizes entries it originally wrote. (Captures never cross the mesh — they are local ingest, not shared memory.)

<details>
<summary>Convergence guarantees</summary>

- **Stable identity.** Propagated entries keep their origin UUID — never re-minted on ingest.
- **No loops.** Re-receiving a known id is a no-op. Entries tagged with your own instance's origin are skipped. `A → B → A` terminates; `A → B → C` propagates.
- **Convergence.** Concurrent edits settle last-writer-wins on `version`; deletes propagate as tombstones. The topology can contain cycles safely.

Pinned by tests in `tests/test_feeds.py`.

</details>

---

## Compile mode

Mesh is one half of the symmetry: many agents converging on one shared truth. Compile mode is the other half — one shared truth converging on the code it describes. Where the mesh keeps instances consistent with each other, compile mode keeps memory consistent with the repo.

Most agent memory is **authored**: a human or agent writes a note, and it slowly decays as it ages and goes unread. That's right for judgement, incidents, and intent — knowledge with no ground truth to check against. But a lot of what agents "remember" about a codebase is really a *description of code that already exists* — and that has a ground truth. **Compiled** memory is anchored to it.

A pre-commit hook walks changed files with a deterministic AST compiler (no LLM), emits one **anchor** per symbol — module, function, class — and hashes each symbol's span. Each anchor mints or refreshes a `compiled` memory stamped with that hash and the commit SHA. When the code changes, the hash changes, and the note doesn't decay — it **recompiles**. Memory that's wrong about the code is rebuilt, not slowly forgotten.

**Authored and compiled are endpoints of a continuum, not two modes.** They share one store, one search index, one API. A note can sit anywhere between — an authored insight that an agent later grounds against a symbol, a compiled fact a human annotates. The same `GET /memory/search` returns both.

**The knowledge graph** is what makes the continuum real. Memories and code anchors are nodes of one heterogeneous graph; edges are typed:

- `grounds` — an anchor grounds a memory in real code
- `relies_on` — one node's meaning depends on another's (the dependency graph of meaning)
- `applies_to` — an authored note applies to a region of code
- `corroborates` / `contradicts` — agreement and tension between notes

Invalidation propagates **backward along `relies_on`**, exactly like `gcc -MMD` incremental builds: change `g`, and every compiled note that relies on `g` is marked stale, transitively. The module anchor hashes the file's *shape* (its sorted imports and top-level symbols), not its bytes, so editing one function body doesn't restale the whole module.

**Viability is connectivity — derived, never stored.** There's no "groundedness" score. An ungrounded memory is just a bare node on the graph, and a bare node is forgettable. The more a memory is connected — fresh groundings, corroborations, things that rely on it — the more viable it is; contradictions and stale groundings pull it down:

```
raw   = fresh_grounds + 0.5·backlinks + 0.3·corroborates − contradictions − 0.5·stale_grounds
score = 0           if raw ≤ 0
        1 − 2^(−raw) otherwise
```

So a fresh, grounded note that nothing disputes scores well; the moment something contradicts it the score collapses toward zero. The computation is live — `GET /graph/:id` recomputes it from the current edges every time, so nothing can go stale behind your back.

**Why you can trust it.** A compiled note carries the source SHA it was built from. Freshness is a hash comparison, not a judgement call: `POST /compile/check` answers *fresh / stale / unknown* per symbol. Fresh means the code hasn't moved since the note was built — you can act on the note without re-reading the code. That's the whole point: trustworthy enough to *not* check.

**Setup is one line — or just ask.** Tell any connected agent *"set up compile mode"* and it calls the `compile_setup` MCP tool, which hands back the installer. Or run it yourself from the repo root:

```bash
# installs a pre-commit hook: a single self-contained, stdlib-only Python file.
# no `pip install` in your repo, and it's a safe no-op until creds are set.
curl -fsSL "$ARTEL/compile/install.sh" | sh
export ARTEL_AGENT_ID=myagent ARTEL_AGENT_KEY=… ARTEL_PROJECT=myrepo

# seed the whole repo once; later commits compile only what changed
python3 "$(git rev-parse --show-toplevel)/.git/hooks/artel_compile.py" --all

# inspect compile health and the graph
curl "$ARTEL/compile/stale?project=myrepo"        # notes whose code moved out from under them
curl "$ARTEL/graph/$NODE_ID"                       # node, edges, live viability
```

Every property above — SHA freshness, `relies_on` invalidation, module-shape stability across body edits, viability collapsing on contradiction, compiled memory never decaying or merging — is pinned by `tests/test_compile.py`. The compiler is deterministic and LLM-free, so the tests are exact, not probabilistic.

---

## Archivist

Optional background process — the server works without it. It is the curator of the shared store and the only writer that turns raw captures into memory.

**With LLM configured:** compacts the capture queue into clean, deduplicated, provenance-tagged memory (minor pass) and consolidates provisional entries over time — merging duplicates, corroborating across sessions, promoting stable knowledge (major pass). Detects semantic conflicts on write and merges them; periodically synthesizes cross-agent findings into shared doc entries.

**Without LLM (passive):** confidence decay and type promotion (memory → doc) based on age and read frequency. Captures are left on the queue for a later LLM-configured run rather than discarded.

**Adaptive decay:** every `GET /memory/:id` read increments a heat counter. Before decaying an entry the archivist computes `heat = read_count × 0.9^(weeks_since_last_read)` — entries above the threshold are skipped. The archivist also records six health metrics per cycle (utilization rate, decay regret, synthesis and merge counts, net growth, contradictions) for trend analysis.

A single archivist holds a lease per deployment, so only one curates at a time. Supports Anthropic and any OpenAI-compatible provider.

---

## Dashboard

Browse your notes, manage tasks, read inboxes, and see what your agents are up to — from a browser. Access at `http://<host>:8000/ui`.

![Dashboard](docs/dash_dashboard.png)

<table>
<tr>
<td width="50%">

![Tasks tab](docs/dash_tasks.png)

</td>
<td width="50%">

![Messages tab](docs/dash_messages.png)

</td>
</tr>
<tr>
<td width="50%">

![Agents tab](docs/dash_agents.png)

</td>
<td width="50%">

![Sessions tab](docs/dash_sessions.png)

</td>
</tr>
</table>

---

## Memory

```python
import httpx

agent = httpx.Client(
    base_url="http://<host>:8000",
    headers={"x-agent-id": "my-agent", "x-api-key": "my-key"},
)

agent.post(
    "/memory",
    json={
        "content": "orders-service p99 spiked at 03:14 UTC. root cause: missing index on customer_id",
        "tags": ["incident", "orders"],
        "confidence": 1.0,
    },
)

results = agent.get("/memory/search", params={"q": "orders latency root cause"}).json()
```

Entries carry confidence scores (0.0–1.0) that decay if not reinforced. Provenance tracks which agent wrote each entry and from which parents. Call `POST /sessions/handoff` before going idle and `GET /sessions/handoff/:id` to resume with full context.

---

## Claude Code (MCP)

The onboard script writes `.mcp.json` automatically, and the [plugin](#the-claude-code-plugin--ambient-memory) wires this up for you. Manual config:

```json
{
  "mcpServers": {
    "artel": {
      "type": "http",
      "url": "http://<host>:8000/mcp",
      "headers": {
        "x-agent-id": "<agent-id>",
        "x-api-key": "<api-key>"
      }
    }
  }
}
```

Artel also supports OAuth 2.1 (dynamic client registration, PKCE, client credentials) for clients that require it. See `/mcp` for the live tool list.

### One-click install

Both buttons install a config with placeholders — replace `YOUR_ARTEL_HOST` with your instance, along with the agent id and key, once it lands in your editor.

[![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/install-mcp?name=artel&config=eyJ1cmwiOiAiaHR0cHM6Ly9ZT1VSX0FSVEVMX0hPU1QvbWNwIiwgImhlYWRlcnMiOiB7IngtYWdlbnQtaWQiOiAiWU9VUl9BR0VOVF9JRCIsICJ4LWFwaS1rZXkiOiAiWU9VUl9BUElfS0VZIn19)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Artel-0098FF?logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%20%22artel%22%2C%20%22type%22%3A%20%22http%22%2C%20%22url%22%3A%20%22https%3A%2F%2FYOUR_ARTEL_HOST%2Fmcp%22%2C%20%22headers%22%3A%20%7B%22x-agent-id%22%3A%20%22YOUR_AGENT_ID%22%2C%20%22x-api-key%22%3A%20%22YOUR_API_KEY%22%7D%7D)

---

## OpenCode (MCP)

[OpenCode](https://opencode.ai) uses SSE MCP transport (not Streamable HTTP). The onboard script detects OpenCode automatically and prints the right config:

```bash
curl -fsSL http://<host>:8000/onboard | sh
```

Manual config for `opencode.json` or `~/.config/opencode/config.json`:

```json
{
  "mcp": {
    "artel": {
      "type": "sse",
      "url": "http://<host>:8001/sse/",
      "headers": {
        "x-agent-id": "<agent-id>",
        "x-api-key": "<api-key>"
      }
    }
  }
}
```

The MCP port defaults to `8001` (separate from the REST API on `8000`). Start it with `MCP_TRANSPORT=sse artel-mcp`. A matching push-layer plugin for opencode lives in [`integrations/opencode/`](integrations/opencode/).

## ACP editors (Zed and friends)

[ACP](https://agentclientprotocol.com) — the Agent Client Protocol — is orthogonal to MCP, not a competitor. MCP points *downward*, from an agent to its tools and data. ACP points *upward*, from an editor or human to an agent. An ACP agent still uses MCP tools, so **anything reachable over ACP can already use Artel through the MCP server above** — no additional adapter, no extra process.

If your editor speaks ACP and the agent behind it supports MCP, point that agent at Artel exactly as shown in the Claude Code or OpenCode sections and it reads and writes the same pad as everything else.

There is deliberately no `artel-acp` server. Artel is a coordination backend, not an agent — it has no LLM loop of its own — so acting *as* an ACP agent would be a category mismatch.

### Wake daemon

`artel-watch` subscribes to the event stream and spawns `opencode` (or any configured command) when a message arrives for your agent — so other agents can reach you when you're not actively running:

```bash
pip install artel
MCP_AGENT_ID=my-agent MCP_AGENT_KEY=my-key ARTEL_URL=http://<host>:8000 artel-watch
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ARTEL_URL` | `http://localhost:8000` | Artel server |
| `MCP_AGENT_ID` | | Agent identity (also: `ARTEL_AGENT_ID`) |
| `MCP_AGENT_KEY` | | API key (also: `ARTEL_KEY`) |
| `ARTEL_WAKE_CMD` | `opencode` | Command to spawn when a message arrives |
| `ARTEL_DEBOUNCE` | `30` | Minimum seconds between spawns |

As a systemd user unit — call `inbox_cron_setup()` from within a session for a pre-filled unit file.

### Inbox resource subscription

Artel exposes `artel://inbox/<agent-id>` as an MCP resource. Subscribe to it and the server pushes `notifications/resources/updated` whenever a message arrives, without polling.

---

## REST API

All requests require `X-Agent-ID` and `X-API-Key` headers (except `/agents/self-register` and `/onboard`).

**[Full REST reference →](https://artel.run/reference/rest/)** — every endpoint, generated from the OpenAPI schema.
**[MCP tool reference →](https://artel.run/reference/mcp-tools/)** — all 47 tools an agent can call.

A running server also serves interactive docs at `/docs` and the raw schema at [`openapi.json`](openapi.json).

---

## Configuration

Configured entirely through environment variables (or a `.env` file). The essentials:

| Variable | Description |
|----------|-------------|
| `AGENT_KEYS` | `agent-id:api-key` pairs, comma-separated. Optional `:proj1;proj2` suffix scopes an agent to projects. |
| `UI_PASSWORD` | Password for the dashboard. |
| `ANTHROPIC_API_KEY` | Enables the archivist. Without it, Artel runs in passive mode. |
| `REGISTRATION_KEY` | Required by `/agents/self-register`. Unset disables open registration. |
| `PUBLIC_URL` | Externally reachable base URL, used in OAuth metadata and onboarding. |

**[Full configuration reference →](https://artel.run/reference/configuration/)** — all 56 settings across the server, MCP adapter, and archivist, generated from the settings classes.

---

## Development

```bash
uv sync --dev
uv run pytest tests/ -v
```

---

## License

MIT. See [LICENSE.md](LICENSE.md).
