# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![Glama](https://glama.ai/mcp/servers/NicolasPrimeau/artel/badges/score.svg)](https://glama.ai/mcp/servers/NicolasPrimeau/artel)
[![smithery badge](https://smithery.ai/badge/nicolas-primeau/artel)](https://smithery.ai/servers/nicolas-primeau/artel)
[![Docs](https://img.shields.io/badge/docs-artel-teal)](https://artel.run)

**Your fleet's smart notepad — one that learns.**

One pad that you and every agent you run write into. Whatever any of you figures out gets written down once and handed back the moment it matters: the gotcha about *this* file right before you edit it, what you decided last Tuesday when you sit back down today, the thing another agent already learned the hard way at 3am. Nothing to file, nothing to tag, nothing to remember to look up.

A normal notepad waits to be opened. This one speaks up.

And it doesn't just accumulate. A background archivist reads what piles up — merging notes that say the same thing, connecting findings nobody thought to link, letting stale things fade, promoting what keeps proving true. The pad gets sharper the more the fleet uses it.

That's the compounding bit: what one session learns at 3am, every other agent already knows by morning. Nobody solves the same thing twice.

**It's yours.** You run it, on your own machine. None of it goes to anyone's cloud.

## What that looks like

| An agent is about to… | Artel says | who wrote it |
|---|---|---|
| edit `auth.py` | "the token refresh silently no-ops when the clock skews" | a different agent, last month |
| start work Monday | "Friday you stopped mid-migration; here's where" | you, before the weekend |
| debug a flaky test | "seen in March — it was the shared fixture, not the test" | an agent on another machine |
| ask a question | the three notes that answer it, before it finishes typing | whoever hit it first |

Nobody opened a file to find any of that, and nobody had to know who to ask.

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
- [Blueprints — notes that run](#blueprints--notes-that-run)
- [Decisions — the append-only log](#decisions--the-append-only-log)
- [Mesh](#mesh)
- [Compile mode](#compile-mode)
- [Archivist](#archivist)
- [Dashboard](#dashboard)
- [Notes over HTTP](#notes-over-http)
- [Claude Code (MCP)](#claude-code-mcp)
- [OpenCode (MCP)](#opencode-mcp)
- [ACP editors (Zed and friends)](#acp-editors-zed-and-friends)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Development](#development)

---

## Features

Grouped by what they do for the fleet, not by what they are.

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

**One pad, many agents**

- **Everything shares it** — anything that speaks HTTP or MCP reads and writes the same notes, whatever machine or LLM it runs on, so what one session learns the rest already know.
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

<!-- covers: captures -->
## Capture

The best notes are the ones you never got around to writing. Capture is the pad writing them for you: what happened in a session becomes durable notes, without slowing anything down and without dumping raw noise into the pad.

The rest of this section is how that's kept honest.

**A two-tier write.** Agents don't reliably write memories back, and pouring a high-pace firehose straight into `memory` would cost an embedding per raw slice and pollute both search and the mesh. So capture lands in a separate **ingest queue** (`captures`) that is deliberately *not* embedded, *not* full-text indexed, *not* replicated over the mesh, and *not* returned by search. Memory is protected structurally: **the archivist is the only path from the queue into memory.**

**Off the hot path.** The `Stop` and `PreCompact` hooks do one thing — append the session payload to a local spool file and fork a detached drainer, then exit (~10 ms, no parsing, no network). The detached drainer compresses each session's new transcript slice (keeps the reasoning, drops bulky tool output), then ships it to the queue. The spool is a durable write-ahead log: if a drainer dies, the next hook's drainer picks up where it left off. Triggers are `Stop` (throttled by a per-session cursor and a size floor) and `PreCompact` (a forced flush right before context is evicted) — **never `SessionEnd`**, because agent sessions rarely end cleanly.

**Leveled compaction (LSM-style).** The archivist drains the queue and integrates each slice into memory — extracting durable facts, reconciling against what already exists (update rather than duplicate), and attaching session provenance. A second, less frequent pass consolidates the provisional entries: merging duplicates, raising confidence when independent sessions corroborate the same fact, reconciling contradictions, and promoting stable knowledge — scoped to the recent delta so the cost stays bounded. Raw captures → provisional memory → consolidated, canonical memory, refined over time.

The net effect: memory quality is decoupled from write volume. Writing fast only fills the queue; only the archivist's judgment turns a capture into memory.

---

<!-- covers: blueprints -->
## Blueprints — notes that run

A `skill` note says how to do something. A **blueprint** is that same procedure compiled into something the fleet can actually execute: template tasks plus the dependencies between them, instantiated as a task DAG that expands itself as it goes.

```bash
blueprint_list()                                   # what's available
blueprint_instantiate("weekly-audit", {"repo": "artel"})   # start a run
blueprint_run(run_id)                              # where it got to
```

Instantiating materializes only the root wave. As tasks complete, a **server-side reactor** expands what comes next — including `foreach` fan-out, where one node completing with a list of five items becomes five sibling tasks. The shape of the run isn't known in advance; it's discovered while running.

**Completion contracts.** A node can require that finishing it produces something specific, checked server-side before the run advances. Three kinds:

| Check | What it verifies |
|---|---|
| `payload` | The completion body has the declared shape — required fields, array minimums. |
| `sqlite` | A query against the store returns what the node promised. |
| `git` | The repository actually changed. The baseline is captured when the task is **created**, so "I changed it" is falsifiable rather than asserted. |

The `git` check is the one that matters most: a perfectly-shaped payload with no corresponding commit does **not** advance the run.

**Lowering.** Nodes that are purely mechanical can carry a `run` action the server executes itself — no model, no agent, no tokens. `lowered_fraction` reports how much of a blueprint runs that way; `register_action()` adds new kinds. The goal is that agents are spent on judgement, not on plumbing.

**Where they come from.** You can write one, or the archivist can compile a prose `skill` note into a blueprint — with a validator-driven repair loop, so what it emits is runnable rather than plausible.

---

<!-- covers: decisions -->
## Decisions — the append-only log

Notes decay, merge, and get rewritten by the archivist. That is right for knowledge and wrong for the record of what you chose and why: a decision that quietly changes later is worse than no record at all.

So decisions are a separate, **append-only** primitive. They are never merged, never decayed, never edited.

```bash
decision_write(decision="use SQLite, not Postgres",
               rationale="single-file backup and WAL are worth more than concurrent writers here",
               alternatives=["Postgres", "DuckDB"])
decision_list()      # what has been decided, newest first
decision_get(id)     # one decision in full
```

Each record carries the choice, the reasoning, the alternatives considered, who made it, and optionally the task it came out of. When someone asks six months later why the store is a single file, the answer is on the record with its alternatives — instead of being reconstructed, badly, from a merged note.

---

<!-- covers: mesh -->
## Mesh

One notepad, several machines — laptop, desktop, the box under the stairs — with no cloud in the middle and no "main" copy. Write on either side, offline if you like; they reconcile when they can see each other.

Each instance publishes its notes as Atom and JSON Feed. Link two and they replicate as a CRDT — keyed by immutable id, idempotent on ingest, no central coordinator. LAN peers discover each other via mDNS (`_artel._tcp.local.`) and link with one click. Each instance's archivist only synthesizes entries it originally wrote. (Captures never cross the mesh — they are local ingest, not shared memory.)

<details>
<summary>Convergence guarantees</summary>

- **Stable identity.** Propagated entries keep their origin UUID — never re-minted on ingest.
- **No loops.** Re-receiving a known id is a no-op. Entries tagged with your own instance's origin are skipped. `A → B → A` terminates; `A → B → C` propagates.
- **Convergence.** Concurrent edits settle last-writer-wins on `version`; deletes propagate as tombstones. The topology can contain cycles safely.

Pinned by tests in `tests/test_feeds.py`.

</details>

### Subscribing to the outside world

<!-- covers: feeds -->
Confusingly, "feed" means two things here. Above, it is how instances replicate to each other. It is also how the pad reads things nobody on your fleet wrote:

```bash
feed_subscribe("https://example.com/blog/atom.xml")
feed_list()
feed_unsubscribe(feed_id)
```

Point it at any RSS or Atom source — a changelog, a security advisory list, a release feed — and new items are polled and land as notes, searchable next to everything else and subject to the same decay. A dependency's breaking-change post is in the pad before an agent trips over it.


---

<!-- covers: compile, graph -->
## Compile mode

Notes about code go stale the moment someone edits the code. A note that says "the retry lives in `client.py`" is worse than no note at all once the retry moves — it sends you confidently to the wrong place.

So pin those notes to the code itself. A **compiled** note is anchored to a symbol; when that symbol changes, the note re-derives instead of quietly rotting. It doesn't decay with age, because age was never what made it wrong.

Most notes are **authored** — judgement, incidents, intent — and those have no ground truth to check against, so they decay if they go unread. Compiled notes do have one, so they get held to it. (Mesh is the mirror image: many machines converging on one set of notes, where this is one set of notes converging on the code it describes.)

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

<!-- covers: archivist -->
## Archivist

This is the part that makes the pad learn. It reads what accumulates and works it into something better: merging notes that say the same thing, connecting findings across sessions, resolving contradictions, letting unused knowledge fade, and promoting what keeps proving true.

It is optional — the server works fine without it, you just get a pad that remembers instead of one that improves. It is also the only writer allowed to turn raw captures into notes.

**With LLM configured:** compacts the capture queue into clean, deduplicated, provenance-tagged memory (minor pass) and consolidates provisional entries over time — merging duplicates, corroborating across sessions, promoting stable knowledge (major pass). Detects semantic conflicts on write and merges them; periodically synthesizes cross-agent findings into shared doc entries.

**Without LLM (passive):** confidence decay and type promotion (memory → doc) based on age and read frequency. Captures are left on the queue for a later LLM-configured run rather than discarded.

**Adaptive decay:** every `GET /memory/:id` read increments a heat counter. Before decaying an entry the archivist computes `heat = read_count × 0.9^(weeks_since_last_read)` — entries above the threshold are skipped. The archivist also records six health metrics per cycle (utilization rate, decay regret, synthesis and merge counts, net growth, contradictions) for trend analysis.

A single archivist holds a lease per deployment, so only one curates at a time. Supports Anthropic and any OpenAI-compatible provider.

---

<!-- covers: pulse, logs -->
## Dashboard

Browse your notes, manage tasks, read inboxes, and see what your agents are up to — from a browser. Access at `http://<host>:8000/ui`.

Two tabs are purely operational: **logs**, where agents ship what they want kept for a human to read, and **pulse**, a live view of how much the fleet is actually doing. Both are read-mostly and exist so a quiet fleet is visibly quiet rather than ambiguously so.

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

## Notes over HTTP

Anything that speaks HTTP can read and write the pad. No SDK, no framework:

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
