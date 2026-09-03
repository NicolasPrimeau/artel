#!/usr/bin/env bash
# Dry-run is ON by default: the LLM step returns the prompt it WOULD send instead of
# sending it. Set ASK_DRY_RUN=0 to answer for real (spends OpenRouter tokens).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$(cd ../.. && pwd)"
export ASK_DB="${ASK_DB:-$HOME/.cache/artel-ask/artel-ro.db}"
export ASK_ARTEL_URL="${ASK_ARTEL_URL:-http://localhost:8000}"
export ASK_AGENT_ID="${ASK_AGENT_ID:-archivist}"
export ASK_API_KEY="${ASK_API_KEY:-$(python3 -c "
from dotenv import dotenv_values
for p in (dotenv_values('$ROOT/.env').get('AGENT_KEYS') or '').split(','):
    k=p.strip().split(':')
    if len(k)>=2 and k[0]=='archivist': print(k[1]); break")}"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(grep -m1 '^OPENROUTER_API_KEY=' "$ROOT/.env" | cut -d= -f2-)}"
export ASK_DRY_RUN="${ASK_DRY_RUN:-1}"
exec uv run --project "$ROOT" uvicorn server:app --host 0.0.0.0 --port 8055
