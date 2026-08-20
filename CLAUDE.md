# Artel

A self-hosted, self-organizing mesh for AI agent fleets — shared memory, session continuity, agent-to-agent communication, cross-instance feed meshing, and async archival synthesis across machines and LLM providers.

## What It Is

Artel is a self-hosted server that gives a fleet of AI agents a self-organizing shared memory and coordination layer that meshes across instances with no central coordinator. Any agent that can make HTTP calls can participate — Claude Code, AutoGen, raw API scripts, anything. Agents read and write memory, pass messages, claim tasks, and emit events. An async archivist agent watches all activity and synthesizes connections no individual agent can see.

## Stack

- Python 3.11+ (CI tests 3.11/3.12/3.13), FastAPI, SQLite (WAL mode), sqlite-vec (embeddings)
- MCP adapter on top of core REST API
- Self-hosted, accessible from all machines

## Layout

```
artel/
  server/       — FastAPI app, routes, auth
  store/        — SQLite models, migrations
  archivist/    — async synthesis agent
  mcp/          — MCP adapter over REST
scripts/
  migration/    — DB migrations
docs/
  plan.md       — execution plan
  spec.md       — protocol and data model spec
  architecture.md — system design
.claude/
  memory/       — agent memory
  skills/       — project skills
```

## Core Primitives

- **Memory** — shared knowledge store with embeddings, confidence scores, provenance
- **Tasks** — create/claim/complete units of work across agents
- **Messages** — direct agent-to-agent async inbox
- **Events** — pub/sub stream for real-time coordination

## Agent Identity

API key + `agent_id` string. No framework coupling. Any HTTP client participates.

## Conventions

- Conventional commits (feat:, fix:, refactor:, docs:)
- No secrets in files — env vars only
- No comments or docstrings
- Pydantic models, no hardcoded strings

## Documentation style

Concise but flowing. Prose that connects, not bullets that stack.

- **Say a thing once.** The archivist's merge/decay/promote list was once spelled
  out four times in near-identical words across the hero, a feature bullet, and two
  adjacent paragraphs of its own section. Detail belongs in the section that owns
  it; everywhere else points at that section.
- **Lead with what it does for the reader**, then how it works. A section opening
  on its own internals assumes a reader who already knows why they are there.
- **Paragraphs over fragments.** A bullet list is right for genuinely parallel
  items and wrong for an argument — if the bullets need to be read in order, they
  are a paragraph.
- **Cut the throat-clearing.** "It is worth noting that", "in order to", a sentence
  restating the heading. If a sentence survives deletion unmissed, delete it.
- Reference pages are generated (`scripts/gen_docs.py`) — never hand-write what a
  docstring, the OpenAPI schema, or a Settings class already states.
- New primitives need prose, not just docstrings: `scripts/check_docs.py` fails
  until a page claims the surface with `<!-- covers: name -->`.

## Running

```bash
uv run python -m artel.server
```

