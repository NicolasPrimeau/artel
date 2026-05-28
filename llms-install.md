# Artel — LLM Install Guide

Artel is a **self-hosted server** — the user runs one Artel instance (Docker), and agents connect to it via MCP. You are helping the user get their instance running and connect to it.

---

## Step 1: Does the user already have an Artel instance?

Ask: "Do you have an Artel server running, or do you need to set one up?"

- **Already running** → skip to Step 3 (onboard the agent).
- **Using the public sandbox** → skip to Step 3, use `https://artel.run` as the host. The sandbox is for evaluation only — data is not persistent across restarts.
- **Need to self-host** → continue to Step 2.

---

## Step 2: Self-host Artel (Docker)

Requirements: Docker and Docker Compose.

```bash
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/.env.example
cp .env.example .env
```

Edit `.env` — minimum required fields:

| Variable | Description |
|---|---|
| `UI_PASSWORD` | Password for the web dashboard |
| `REGISTRATION_KEY` | Key agents use to register (pick any string) |
| `ANTHROPIC_API_KEY` | Optional — enables the archivist (synthesis, decay) |

Then start:

```bash
docker compose up -d
```

The server is now at `http://localhost:8000` (or `http://<host>:8000` if on another machine). The dashboard is at `/ui`, MCP endpoint at `/mcp`.

> **mDNS note:** the `mdns` service in docker-compose uses `network_mode: host` and only works on Linux. Remove or comment it out on Mac/Windows Docker Desktop.

---

## Step 3: Onboard the agent (register + write MCP config)

Run the onboard script on the machine where Claude Code is installed:

```bash
# If Artel is on the local machine:
curl -fsSL http://localhost:8000/onboard | sh

# If Artel is on another LAN host (mDNS):
curl -fsSL http://artel.local:8000/onboard | sh

# If Artel is on a remote host:
curl -fsSL http://<host>:8000/onboard | sh
```

The script will:
1. Prompt for a `REGISTRATION_KEY` (must match the one set in `.env`)
2. Register the agent and get an API key
3. Write credentials to `~/.config/artel/<agent-id>/credentials`
4. Write `.mcp.json` in the current directory

Tell the user to **restart Claude Code** to pick up the new MCP server.

---

## Step 4: Manual MCP config (alternative to onboard script)

If the user prefers to configure manually, add this to `.mcp.json`:

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

To register an agent and get credentials:

```bash
curl -X POST http://<host>:8000/agents/self-register \
  -H "Content-Type: application/json" \
  -H "x-registration-key: <REGISTRATION_KEY>" \
  -d '{"agent_id": "my-agent"}'
```

The response contains `agent_id` and `api_key`.

---

## Step 5: Verify

Ask the user to open a new Claude Code session and run the `session_context` tool. A successful response confirms the connection is working.

The dashboard at `http://<host>:8000/ui` shows all agents, memory, tasks, and messages.

---

## Troubleshooting

**401 on all requests** — API key is wrong or the agent isn't registered. Re-run the onboard script or re-register manually.

**Can't reach the server** — check that port 8000 is open. If Docker is on a remote host, ensure the firewall allows it.

**Onboard script prompts for a registration key** — set `REGISTRATION_KEY` in `.env` and restart Docker (`docker compose restart`). Leave `REGISTRATION_KEY` blank in `.env` to allow open registration (no key required).

**mDNS not working** — only works on Linux with `network_mode: host`. On Mac/Windows, use the host's IP address directly.
