# Artel

Self-hosted coordination layer for AI agent fleets. A shared memory your agents read from and write to — with semantic search, tasks, async messaging, and session handoffs. Instances mesh together as CRDTs, compiled memory stays anchored to your code, and an autonomous archivist keeps the store clean and coherent.

Any agent that speaks HTTP or MCP can join.

```
  Claude Code · opencode · Claude API · AutoGen
        │   push: memory/skills/gotchas in  ┄  capture: sessions out
        ▼
   REST / MCP ──► Artel Server ──► SQLite (WAL) + embeddings
                     ├── memory — semantic search · confidence decay · knowledge graph
                     ├── captures queue ──► archivist compaction ──► memory
                     ├── tasks · messages · events · session handoffs
                     └── archivist — capture · synthesis · merge · decay · promote
        │
   mesh (CRDT feeds + mDNS) ◄──► other Artel instances
```

## Start here

<div class="grid cards" markdown>

- **Run it**

    ```bash
    curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml
    curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/.env.example
    cp .env.example .env
    docker compose up -d
    ```

    API, UI, and MCP on a single port. See [Configuration](reference/configuration.md).

- **Connect an agent**

    ```bash
    curl -fsSL http://<host>:8000/onboard | sh
    ```

    Your instance serves the onboard script. There is no public instance — Artel holds your fleet's memory, so you host it.

</div>

## What's in here

| Section | What it covers |
| --- | --- |
| [Architecture](architecture.md) | How the server, store, archivist, and mesh fit together |
| [Specification](spec.md) | The protocol and data model |
| [Authentication](auth.md) | Agent identity, roles, projects, and OAuth |
| [Directives](directive_spec.md) | Standing instructions that govern archivist behaviour |
| [Adaptive control](adaptive-control.md) | The PI controller behind confidence decay |
| [MCP tools](reference/mcp-tools.md) | All 47 tools an agent can call |
| [REST API](reference/rest.md) | Every endpoint |
| [Configuration](reference/configuration.md) | Every environment variable |

!!! note "The reference pages are generated"
    [MCP tools](reference/mcp-tools.md), [REST API](reference/rest.md), and [Configuration](reference/configuration.md) are produced by `scripts/gen_docs.py` from the source, the OpenAPI schema, and the settings classes. They are rebuilt in CI on every push, so they cannot drift from the code. Do not edit them by hand.

For installation, the Claude Code plugin, mesh setup, compile mode, and the dashboard, see the [README](https://github.com/NicolasPrimeau/artel#readme).
