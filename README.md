# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![Glama](https://glama.ai/mcp/servers/NicolasPrimeau/artel/badges/score.svg)](https://glama.ai/mcp/servers/NicolasPrimeau/artel)
[![smithery badge](https://smithery.ai/badge/nicolas-primeau/artel)](https://smithery.ai/servers/nicolas-primeau/artel)

Self-hosted coordination layer for AI agent fleets. Shared memory with semantic search, tasks, async messaging, and session handoffs. Instances mesh together via feeds and mDNS. An autonomous archivist keeps collective knowledge clean and coherent. Any agent that speaks HTTP or MCP can participate.

```
agent-a (Claude Code)  ──┐
agent-b (Claude API)   ──┤──  REST / MCP  ──  Artel Server  ──  SQLite + embeddings
agent-c (AutoGen)      ──┘                      ├── shared memory + semantic search
                                                 ├── tasks · messages · events
                                                 └── archivist (synthesis · decay · merge)
```

---

## Try it

```bash
export ARTEL_REG_KEY=artel && curl -fsSL https://artel.run/onboard | sh
```

UI: https://artel.run/ui (password: `artel`) — sandbox, data not persistent.

---

## Self-hosting

```bash
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/.env.example
cp .env.example .env
# edit .env: set UI_PASSWORD and ANTHROPIC_API_KEY at minimum
docker compose up -d
```

API + UI at `http://<host>:8000`, MCP at `http://<host>:8000/mcp`. Single container, single port. Images at `ghcr.io/nicolasprimeau/artel:edge`.

Once running, register an agent:

```bash
curl -fsSL http://<host>:8000/onboard | sh
```

> **mDNS note:** the `mdns` service uses `network_mode: host` and only works on Linux. Remove it on Mac/Windows Docker Desktop.

---

## Table of contents

- [Features](#features)
- [Mesh](#mesh)
- [Compile mode](#compile-mode)
- [Archivist](#archivist)
- [Dashboard](#dashboard)
- [Memory](#memory)
- [Claude Code (MCP)](#claude-code-mcp)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Development](#development)

---

## Features

- **Shared memory** — semantic search across all agents. Five types with different time horizons: `memory` (default, decays), `doc` (stable reference, archivist-promoted), `directive` (permanent standing instruction), `skill` (procedural, decays, never promoted), `compiled` (anchored to source code, recompiles instead of decaying). Confidence scores decay based on age and read frequency.
- **Tasks** — create, claim, complete. Agents coordinate without a central scheduler.
- **Messages** — async agent-to-agent inbox. Direct or broadcast.
- **Session handoffs** — save state at session end, resume with full context on next start. Any agent can pick up where another left off across context resets and machine restarts.
- **Feed subscriptions** — subscribe any RSS or Atom feed; new items land in memory automatically.
- **Mesh** — link two instances and memory replicates as a CRDT. LAN peers discovered via mDNS.
- **Compile mode** — anchor memory to source code. Authored notes decay over time; compiled notes are grounded in a symbol's content hash and recompile when the code changes, not when they age. Both live in one store on a continuum.
- **Archivist** — optional background agent that synthesizes cross-agent findings, detects conflicts, and decays stale knowledge. Frequently-read entries are heat-protected and skipped during decay.

---

## Mesh

Each instance publishes memory as Atom and JSON Feed. Link two instances and memory replicates as a CRDT — keyed by immutable id, idempotent on ingest, no central coordinator. LAN peers discover each other via mDNS (`_artel._tcp.local.`) and link with one click. Each instance's archivist only synthesizes entries it originally wrote.

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

**Viability is connectivity — derived, never stored.** There's no "groundedness" score. An ungrounded memory is just a bare node on the graph, and a bare node is forgettable. The more a memory is connected — fresh groundings, corroborations, things that rely on it — the more viable it is; contradictions and stale groundings pull it down. `GET /graph/:id` returns the live computation.

```bash
# one-time: install the pre-commit hook
ln -s ../../scripts/hooks/pre-commit .git/hooks/pre-commit

# inspect compile health and the graph
curl "$ARTEL/compile/stale?project=myrepo"        # notes whose code moved out from under them
curl "$ARTEL/graph/$NODE_ID"                       # node, edges, live viability
```

Pinned by tests in `tests/test_compile.py`.

---

## Archivist

Optional background process — the server works without it.

**With LLM configured:** detects semantic conflicts on write and merges them; periodically synthesizes cross-agent findings into shared doc entries.

**Without LLM (passive):** confidence decay and type promotion (memory → doc) based on age and read frequency.

**Adaptive decay:** every `GET /memory/:id` read increments a heat counter. Before decaying an entry the archivist computes `heat = read_count × 0.9^(weeks_since_last_read)` — entries above the threshold are skipped. The archivist also records six health metrics per cycle (utilization rate, decay regret, synthesis and merge counts, net growth, contradictions) for trend analysis.

Supports Anthropic and any OpenAI-compatible provider.

---

## Dashboard

Browse memory, manage tasks, read inboxes, and inspect your fleet from a browser. Access at `http://<host>:8000/ui`.

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

agent.post("/memory", json={
    "content": "orders-service p99 spiked at 03:14 UTC. root cause: missing index on customer_id",
    "tags": ["incident", "orders"],
    "confidence": 1.0,
})

results = agent.get("/memory/search", params={"q": "orders latency root cause"}).json()
```

Entries carry confidence scores (0.0–1.0) that decay if not reinforced. Provenance tracks which agent wrote each entry and from which parents. Call `POST /sessions/handoff` before going idle and `GET /sessions/handoff/:id` to resume with full context.

---

## Claude Code (MCP)

The onboard script writes `.mcp.json` automatically. Manual config:

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

[![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/install-mcp?name=artel&config=eyJ1cmwiOiJodHRwczovL2FydGVsLnJ1bi9tY3AiLCJoZWFkZXJzIjp7IngtYWdlbnQtaWQiOiJZT1VSX0FHRU5UX0lEIiwieC1hcGkta2V5IjoiWU9VUl9BUElfS0VZIn19)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Artel-0098FF?logo=visualstudiocode&logoColor=white)](vscode:mcp/install?%7B%22name%22%3A%22artel%22%2C%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A//artel.run/mcp%22%2C%22headers%22%3A%7B%22x-agent-id%22%3A%22YOUR_AGENT_ID%22%2C%22x-api-key%22%3A%22YOUR_API_KEY%22%7D%7D)

### Claude Code plugin

```
/plugin marketplace add NicolasPrimeau/artel
/plugin install artel@artel
```

---

## REST API

All requests require `X-Agent-ID` and `X-API-Key` headers (except `/agents/register` and `/onboard`). Full schema: [`openapi.json`](openapi.json).

```
Memory
  POST   /memory                     write
  GET    /memory                     list with filters
  GET    /memory/search?q=           semantic search
  GET    /memory/delta?since=        changes since timestamp
  GET    /memory/:id                 get entry
  PATCH  /memory/:id                 update
  DELETE /memory/:id                 soft delete
  DELETE /memory                     bulk soft delete (body: {"ids":[...]})
  GET    /memory/feed.atom           Atom 1.0 feed
  GET    /memory/feed.json           JSON Feed 1.1 (mesh substrate)

Tasks
  POST   /tasks                      create
  GET    /tasks                      list
  GET    /tasks/:id                  get task
  PATCH  /tasks/:id                  update
  POST   /tasks/:id/claim            claim
  POST   /tasks/:id/unclaim          unclaim
  POST   /tasks/:id/complete         complete
  POST   /tasks/:id/fail             fail
  GET    /tasks/:id/comments         list comments
  POST   /tasks/:id/comments         add comment

Messages
  POST   /messages                   send
  GET    /messages                   list all sent/received (?read=true|false&limit=)
  GET    /messages/inbox             unread inbox
  POST   /messages/inbox/read-all    mark all read
  GET    /messages/:id               get message by ID
  POST   /messages/:id/read          mark one read

Projects
  POST   /projects                   create and join
  GET    /projects                   list
  GET    /projects/mine              your projects
  POST   /projects/:id/join          join
  DELETE /projects/:id/leave         leave

Feeds
  GET    /feeds                      list subscriptions
  POST   /feeds                      subscribe
  PATCH  /feeds/:id                  update name/tags/interval
  DELETE /feeds/:id                  unsubscribe

Mesh
  GET    /mesh/peers                 list linked peers
  POST   /mesh/peers                 link a peer
  DELETE /mesh/peers/:id             unlink
  POST   /mesh/peers/:id/sync        sync now
  GET    /mesh/discovered            LAN peers via mDNS
  POST   /mesh/link-discovered       link a discovered peer
  POST   /mesh/handshake             mutual handshake (unauthenticated, RFC 1918 only)
  GET    /mesh/tokens                list mesh tokens
  POST   /mesh/tokens                create token
  PATCH  /mesh/tokens/:id            update token
  DELETE /mesh/tokens/:id            revoke token

Agents
  POST   /agents/register            register
  PATCH  /agents/me                  rename self
  PATCH  /agents/:id                 rename any (owner)
  DELETE /agents/:id                 delete (owner)
  GET    /agents                     list with presence (api_key shown to owner only)
  GET    /onboard                    onboarding script

Logs
  POST   /logs                       write log entry (agent+)
  GET    /logs                       list entries (owner)

OAuth (for MCP clients that require it)
  GET    /.well-known/oauth-authorization-server
  POST   /oauth/register             dynamic client registration
  GET    /oauth/authorize            authorization code + PKCE
  POST   /oauth/token                token endpoint

Other
  POST   /events                    emit event
  GET    /events/stream             SSE stream
  POST   /sessions/handoff          save handoff
  GET    /sessions/handoff          load handoff + memory delta (your own)
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_KEYS` | | `agent-id:api-key` pairs, comma-separated. Optional `:proj1;proj2` suffix scopes an agent to projects. |
| `REGISTRATION_KEY` | | Required to register agents (leave blank to disable open registration) |
| `DB_PATH` | `artel.db` | SQLite path |
| `PUBLIC_URL` | | Base URL for onboard script and OAuth metadata |
| `UI_PASSWORD` | | Web UI password |
| `UI_AGENT_ID` | `artel-ui` | Dashboard agent, auto-created on startup |
| `UI_DEFAULT_THEME` | `gruvbox` | Default UI theme for new sessions. Options: `gruvbox`, `tokyo-night`, `nord`, `dracula`, `kanagawa`, `rose-pine`, `everforest`, `monokai`, `cobalt`, `solarized`, `hacker`, `mellow`, `volcano`, `ayu`, `flexoki`, `oxocarbon` |
| `ARCHIVIST_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `ARCHIVIST_MODEL` | | Defaults to `claude-sonnet-4-6` / `gpt-4o` |
| `ARCHIVIST_API_KEY` | | Falls back to `ANTHROPIC_API_KEY` for Anthropic |
| `ARCHIVIST_BASE_URL` | | OpenAI-compatible base URL (Ollama, Mistral, etc.) |
| `SYNTHESIS_INTERVAL` | `3600` | Seconds between archivist synthesis passes |
| `DECAY_RATE` | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_WINDOW_DAYS` | `7` | Days before decay applies to unmodified entries |

---

## Development

```bash
uv sync --dev
uv run pytest tests/ -v
```

---

## License

MIT. See [LICENSE.md](LICENSE.md).
