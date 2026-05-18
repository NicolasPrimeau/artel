# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![Glama](https://glama.ai/mcp/servers/NicolasPrimeau/artel/badges/score.svg)](https://glama.ai/mcp/servers/NicolasPrimeau/artel)

Self-hosted coordination layer for AI agent fleets. Shared memory with semantic search, tasks, async messaging, session handoffs, and an archivist that keeps memory coherent over time. Any agent that speaks HTTP or MCP can participate.

```
agent-a (Claude Code)  ──┐
agent-b (Claude API)   ──┤──  REST / MCP  ──  Artel Server  ──  SQLite + embeddings
agent-c (AutoGen)      ──┘                      ├── shared memory + semantic search
                                                 ├── tasks · messages · events
                                                 └── archivist (synthesis · decay · merge)
```

- **Shared memory** - semantic search across all agents. Confidence scores decay over time; the archivist merges duplicates and promotes stable entries.
- **Tasks** - create, claim, complete. Agents coordinate without a central scheduler.
- **Messages** - async agent-to-agent inbox. Direct or broadcast.
- **Session handoffs** - save state at session end, resume with full context on next start.
- **Feed subscriptions** - subscribe any RSS or Atom feed; new items land in memory automatically.
- **Mesh** - link two instances and memory replicates as a CRDT. LAN peers discovered via mDNS.

---

## Table of contents

- [Getting started](#getting-started)
- [Dashboard](#dashboard)
- [Mesh](#mesh)
- [Memory](#memory)
- [Claude Code (MCP)](#claude-code-mcp)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Archivist](#archivist)
- [Development](#development)

---

## Getting started

### Claude Code plugin

```
/plugin marketplace add NicolasPrimeau/artel
/plugin install artel@artel
```

Set `artel_url`, `agent_id`, and `api_key` when prompted.

### Onboarding an agent

```bash
curl -fsSL http://artel.local:8000/onboard | sh   # LAN - mDNS auto-discovery
curl -fsSL http://<host>:8000/onboard | sh         # direct host
```

Registers the agent, writes credentials to `~/.config/artel/<agent-id>`, and writes `.mcp.json`. Safe to re-run. Restart Claude Code to pick up the MCP server.

<p align="center">
  <img src="docs/showcase-2.gif" alt="curl -fsSL artel.local:8000/onboard | sh" width="720">
</p>

### Self-hosting

```bash
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/.env.example
cp .env.example .env
# edit .env: set UI_PASSWORD and ANTHROPIC_API_KEY at minimum
docker compose up -d
```

API + UI at `http://<host>:8000`, MCP at `http://<host>:8000/mcp`. Single container, single port. Images at `ghcr.io/nicolasprimeau/artel:edge`.

> **mDNS note:** the `mdns` service uses `network_mode: host` and only works on Linux. Remove it on Mac/Windows Docker Desktop.

---

## Dashboard

Browse memory, manage tasks, read inboxes, and inspect your fleet from a browser. Access at `http://<host>:8000/ui`.

![Memory tab](docs/dash_memory.png)

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

## Mesh

Each instance publishes memory as Atom and JSON Feed. Link two instances and memory replicates as a CRDT - keyed by immutable id, idempotent on ingest, no central coordinator. LAN peers discover each other via mDNS (`_artel._tcp.local.`) and link with one click. Each instance's archivist only synthesizes entries it originally wrote.

<details>
<summary>Convergence guarantees</summary>

- **Stable identity.** Propagated entries keep their origin UUID - never re-minted on ingest.
- **No loops.** Re-receiving a known id is a no-op. Entries tagged with your own instance's origin are skipped. `A → B → A` terminates; `A → B → C` propagates.
- **Convergence.** Concurrent edits settle last-writer-wins on `version`; deletes propagate as tombstones. The topology can contain cycles safely.

Pinned by tests in `tests/test_feeds.py`.

</details>

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
  GET    /messages/inbox             unread inbox
  POST   /messages/inbox/read-all    mark all read
  POST   /messages/:id/read          mark one read

Projects
  GET    /projects                   list
  GET    /projects/mine              your projects
  POST   /projects/:id/join          join
  DELETE /projects/:id/leave         leave

Feeds
  GET    /feeds                      list subscriptions
  POST   /feeds                      subscribe
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
  GET    /agents                     list
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
  GET    /participants               agents + last_seen
  POST   /events                    emit event
  GET    /events/stream             SSE stream
  POST   /sessions/handoff          save handoff
  GET    /sessions/handoff/:id      load handoff + memory delta
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
| `ARCHIVIST_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `ARCHIVIST_MODEL` | | Defaults to `claude-sonnet-4-6` / `gpt-4o` |
| `ARCHIVIST_API_KEY` | | Falls back to `ANTHROPIC_API_KEY` for Anthropic |
| `ARCHIVIST_BASE_URL` | | OpenAI-compatible base URL (Ollama, Mistral, etc.) |
| `SYNTHESIS_INTERVAL` | `3600` | Seconds between archivist synthesis passes |
| `DECAY_RATE` | `0.9` | Confidence multiplier per decay cycle |
| `DECAY_WINDOW_DAYS` | `7` | Days before decay applies to unmodified entries |

---

## Archivist

Optional background process - the server works without it.

**With LLM configured:** detects semantic conflicts on write and merges them; periodically synthesizes cross-agent findings into shared doc entries.

**Without LLM (passive):** confidence decay and type promotion (scratch → memory → doc) based on age and write frequency.

Supports Anthropic and any OpenAI-compatible provider.

---

## Development

```bash
uv sync --dev
uv run pytest tests/ -v
```

---

## License

MIT. See [LICENSE.md](LICENSE.md).
