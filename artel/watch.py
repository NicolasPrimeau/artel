"""artel-watch — wake daemon for OpenCode and other cold-start agents.

Subscribes to the Artel event stream for message.received events addressed
to this agent, then spawns a configurable wake command (default: opencode)
so the agent can process its inbox even when not actively running.

Environment variables:
    ARTEL_URL         Artel server base URL (default: http://localhost:8000)
    MCP_AGENT_ID      Agent identity (also accepts ARTEL_AGENT_ID)
    MCP_AGENT_KEY     API key (also accepts ARTEL_KEY)
    ARTEL_WAKE_CMD    Command to spawn on wake (default: opencode)
    ARTEL_DEBOUNCE    Minimum seconds between spawns (default: 30)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import sys
import time

import httpx

log = logging.getLogger("artel.watch")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    url = os.environ.get("ARTEL_URL", "http://localhost:8000")
    agent_id = os.environ.get("MCP_AGENT_ID") or os.environ.get("ARTEL_AGENT_ID", "")
    api_key = os.environ.get("MCP_AGENT_KEY") or os.environ.get("ARTEL_KEY", "")
    wake_cmd = os.environ.get("ARTEL_WAKE_CMD", "opencode")
    debounce = float(os.environ.get("ARTEL_DEBOUNCE", "30"))

    if not agent_id or not api_key:
        print(
            "artel-watch: set MCP_AGENT_ID + MCP_AGENT_KEY  (or ARTEL_AGENT_ID + ARTEL_KEY)",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(_watch(url, agent_id, api_key, wake_cmd, debounce))


async def _watch(url: str, agent_id: str, api_key: str, wake_cmd: str, debounce: float) -> None:
    stream_url = f"{url.rstrip('/')}/events/stream"
    headers = {
        "x-agent-id": agent_id,
        "x-api-key": api_key,
        "Accept": "text/event-stream",
    }
    last_spawn = 0.0
    delay = 1.0

    log.info("agent=%s  cmd=%r  url=%s", agent_id, wake_cmd, url)

    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as client:
                async with client.stream(
                    "GET",
                    stream_url,
                    headers=headers,
                    params={"type": "message.received"},
                ) as resp:
                    resp.raise_for_status()
                    delay = 1.0
                    log.info("connected — watching for messages addressed to %s", agent_id)

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if not raw:
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        payload = event.get("payload", {})
                        to = payload.get("to", "")
                        if to != agent_id and to != "broadcast" and not to.startswith("project:"):
                            continue

                        now = time.monotonic()
                        if now - last_spawn < debounce:
                            log.debug("debouncing (last spawn %.0fs ago)", now - last_spawn)
                            continue

                        last_spawn = now
                        sender = event.get("agent_id", "?")
                        log.info("message from %s → spawning %r", sender, wake_cmd)
                        subprocess.Popen(shlex.split(wake_cmd))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("disconnected (%s) — reconnect in %.0fs", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


if __name__ == "__main__":
    main()
