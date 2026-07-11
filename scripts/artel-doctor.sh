#!/usr/bin/env bash
# Artel plugin doctor: diagnose config + connectivity so a silent no-op is easy to
# debug. Reads CLAUDE_PLUGIN_OPTION_ARTEL_URL / _AGENT_ID / _API_KEY, falling back to
# ARTEL_URL / ARTEL_AGENT_ID / ARTEL_API_KEY. Never prints the API key.

url="${CLAUDE_PLUGIN_OPTION_ARTEL_URL:-${ARTEL_URL:-}}"
aid="${CLAUDE_PLUGIN_OPTION_AGENT_ID:-${ARTEL_AGENT_ID:-}}"
key="${CLAUDE_PLUGIN_OPTION_API_KEY:-${ARTEL_API_KEY:-}}"

ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; }
info() { printf '  ---  %s\n' "$1"; }

echo "Artel plugin doctor"
[ -n "$url" ] && ok "ARTEL_URL set: ${url}" || { bad "ARTEL_URL not set"; }
[ -n "$aid" ] && ok "AGENT_ID set: ${aid}" || { bad "AGENT_ID not set"; }
[ -n "$key" ] && ok "API_KEY set (${#key} chars)" || { bad "API_KEY not set"; }
[ -z "$url" ] || [ -z "$aid" ] || [ -z "$key" ] && { info "set the three vars, then re-run"; exit 1; }

base="${url%/}"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${base}/health" 2>/dev/null)"
[ "$code" = "200" ] && ok "server reachable (GET /health -> 200)" || bad "server not reachable (GET /health -> ${code:-no response})"

code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  -H "x-agent-id: $aid" -H "x-api-key: $key" "${base}/messages/inbox" 2>/dev/null)"
case "$code" in
  200) ok "credentials valid (GET /messages/inbox -> 200)";;
  401|403) bad "credentials rejected (-> ${code}); check AGENT_ID / API_KEY";;
  *) bad "auth check failed (-> ${code:-no response})";;
esac
