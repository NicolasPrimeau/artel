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

# Only the primary server process curates inline. Skip the dedicated archivist
# service (double-run against one database) AND utility sidecars like the mDNS
# announcer — those run under network_mode: host with no shared DB volume, so an
# inline curator there wins the lease but writes metrics/cursors to a throwaway
# database, silently starving the real archivist.
case "$*" in
    *artel.archivist*|*mdns_announce*) RUN_INLINE_ARCHIVIST=0 ;;
esac

# Run the archivist alongside the server when it has an LLM key — it curates the
# shared memory (merge, decay, promote) so project corpora don't grow unbounded.
# Set RUN_INLINE_ARCHIVIST=0 when a dedicated archivist service already runs it
# (e.g. docker-compose), so two archivists don't race on the same database.
if [ "${RUN_INLINE_ARCHIVIST:-1}" != "0" ] && { [ -n "$ARCHIVIST_API_KEY" ] || [ -n "$ANTHROPIC_API_KEY" ] || [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; }; then
    (
        sleep 20  # let the server come up first
        while true; do
            python -m artel.archivist || true
            sleep 60  # crashed or exited: back off, then resume curating
        done
    ) &
fi

exec "$@"
