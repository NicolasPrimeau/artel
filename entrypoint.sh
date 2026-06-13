#!/bin/sh
set -e

# Generate mcp-agent credentials if AGENT_KEYS is not set.
# docker-compose deployments provide AGENT_KEYS via env_file; this
# only fires when the image is run directly (e.g. docker run, Glama).
if [ -z "$AGENT_KEYS" ]; then
    _key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    export AGENT_KEYS="mcp:${_key}"
    export MCP_AGENT_KEY="${_key}"
fi

# Forward REGISTRATION_KEY -> MCP_REGISTRATION_KEY when not set explicitly.
if [ -z "$MCP_REGISTRATION_KEY" ] && [ -n "$REGISTRATION_KEY" ]; then
    export MCP_REGISTRATION_KEY="$REGISTRATION_KEY"
fi

# Run the archivist alongside the server when it has an LLM key — it curates the
# shared memory (merge, decay, promote) so project corpora don't grow unbounded.
if [ -n "$ARCHIVIST_API_KEY" ] || [ -n "$ANTHROPIC_API_KEY" ]; then
    (
        sleep 20  # let the server come up first
        while true; do
            python -m artel.archivist || true
            sleep 60  # crashed or exited: back off, then resume curating
        done
    ) &
fi

exec "$@"
