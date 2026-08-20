# Connecting clients

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

### Claude Code (MCP)

The onboard script writes `.mcp.json` automatically, and the [plugin](plugin.md) wires this up for you. Manual config:

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

### OpenCode (MCP)

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

### ACP editors (Zed and friends)

[ACP](https://agentclientprotocol.com) — the Agent Client Protocol — is orthogonal to MCP, not a competitor. MCP points *downward*, from an agent to its tools and data. ACP points *upward*, from an editor or human to an agent. An ACP agent still uses MCP tools, so **anything reachable over ACP can already use Artel through the MCP server above** — no additional adapter, no extra process.

If your editor speaks ACP and the agent behind it supports MCP, point that agent at Artel exactly as shown above for Claude Code or OpenCode and it reads and writes the same pad as everything else.

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
