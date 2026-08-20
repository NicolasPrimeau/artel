# Artel

[![CI](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml/badge.svg)](https://github.com/NicolasPrimeau/artel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)
[![Glama](https://glama.ai/mcp/servers/NicolasPrimeau/artel/badges/score.svg)](https://glama.ai/mcp/servers/NicolasPrimeau/artel)
[![Smithery](https://img.shields.io/badge/Smithery-artel-6f4cff)](https://smithery.ai/servers/nicolas-primeau/artel)
[![Docs](https://img.shields.io/badge/docs-artel-teal)](https://artel.run)

**Your fleet's smart notepad — one that learns.**

One pad that you and every agent you run write into. Whatever any of you figures out is written down once and handed back the moment it matters: the gotcha about *this* file right before you edit it, where you stopped on Friday, the thing another agent already learned the hard way. Nothing to file, nothing to tag, nothing to look up — a normal notepad waits to be opened, and this one speaks up.

It also doesn't just accumulate. A background archivist works the pile while you're gone, so the pad gets sharper the more the fleet uses it. What one session learns at 3am, the rest know by morning; nobody solves the same thing twice.

You run it on your own machine. None of it goes to anyone's cloud.

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

A server, a database, and a librarian. Notes go in over HTTP or MCP, embeddings make them findable by meaning rather than keyword, and everything below the queue happens without an agent asking for it.

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

## What's inside

Each of these has a page in the [docs](https://artel.run); this is the map.

| | |
|---|---|
| **[The plugin](https://artel.run/plugin/)** | The half that speaks up — injects the right note at session start, on each prompt, and before you edit a file. |
| **[Capture](https://artel.run/capture/)** | Sessions become notes on their own, spooled in ~10 ms so writing never slows an agent down. |
| **[Archivist](https://artel.run/archivist/)** | The part that learns: merges duplicates, resolves contradictions, decays what stopped being true, promotes what held up. |
| **[Compile mode](https://artel.run/compile-mode/)** | Notes about code pinned to the code, so they re-derive instead of rotting. |
| **[Blueprints](https://artel.run/blueprints/)** | A procedure compiled into a self-expanding task DAG, with contracts the server checks before a run advances. |
| **[Decisions](https://artel.run/decisions/)** | Append-only record of what you chose and why — never merged, never decayed. |
| **[Mesh and feeds](https://artel.run/mesh/)** | Several machines converging as CRDTs, plus RSS/Atom subscriptions from the outside world. |
| **[Dashboard](https://artel.run/dashboard/)** | Browse, search, and watch the fleet from a browser. |

Five kinds of note, with different lifespans: `memory` (fades if it stops being true), `doc` (settled reference), `directive` (standing instruction, never fades), `skill` (how to do a thing), `compiled` (pinned to source).

Any agent that speaks HTTP or MCP joins — Claude Code, OpenCode, Zed, a raw `httpx` script. See [connecting clients](https://artel.run/clients/).

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
