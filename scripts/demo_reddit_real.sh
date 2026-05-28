#!/bin/bash
# Real Claude Code session continuity demo for Reddit
#
# Record:
#   cd /home/nprimeau/projects/artel
#   asciinema rec /tmp/artel-reddit.cast --cols 100 --rows 35 --overwrite -c "bash scripts/demo_reddit_real.sh"
#   agg /tmp/artel-reddit.cast docs/reddit.gif --speed 1.0 --font-size 13

set -e

COLS=100
ROWS=35
MCP_CONFIG="/home/nprimeau/projects/Nimbus/.mcp.json"
FMT="python3 /home/nprimeau/projects/artel/scripts/demo_fmt.py"

_act() {
    local n="$1" title="$2"
    local rule
    rule=$(python3 -c "print('─' * $COLS)")
    local mid=$(( ROWS / 2 ))
    local act_text="ACT $n"
    local act_pad=$(( (COLS - ${#act_text}) / 2 ))
    local title_pad=$(( (COLS - ${#title}) / 2 ))

    printf '\033[2J\033[H'
    printf "\033[%d;1H\033[2m%s\033[0m" $((mid - 2)) "$rule"
    printf "\033[%d;%dH\033[1;33m%s\033[0m" $mid $((act_pad + 1)) "$act_text"
    printf "\033[%d;%dH\033[1;97m%s\033[0m" $((mid + 1)) $((title_pad + 1)) "$title"
    printf "\033[%d;1H\033[2m%s\033[0m" $((mid + 3)) "$rule"
    sleep 2.5
    printf '\033[2J\033[H'
}

_claude() {
    claude -p "$1" \
        --dangerously-skip-permissions \
        --no-session-persistence \
        --mcp-config "$MCP_CONFIG" \
        --output-format stream-json \
        --verbose \
        2>/dev/null | $FMT
}

# ── Intro ──────────────────────────────────────────────────────────────────
printf '\033[2J\033[H\n'
printf "  \033[1;97m╔══════════════════════════════════════════════╗\033[0m\n"
printf "  \033[1;97m║  \033[36mARTEL\033[97m  ─  session continuity               ║\033[0m\n"
printf "  \033[1;97m╚══════════════════════════════════════════════╝\033[0m\n\n"
sleep 1.5
printf "  \033[97mOne Claude Code session ends.\033[0m\n"
sleep 0.6
printf "  \033[2mThe next one picks up exactly where it left off.\033[0m\n"
sleep 2.0

# ── ACT I ─────────────────────────────────────────────────────────────────
_act 1 "SESSION ONE"

_claude "You are starting a Claude Code session on the Nimbus project.
Use these Artel MCP tools in order — call each tool, then continue to the next:

1. mcp__artel__session_context — check for previous context
2. mcp__artel__memory_write — store: content='Procurement API: 100 req/min rate limit. Bulk endpoint /tenders/batch accepts 500 ids. Cursor pagination only — cursor expires in 10 min.', tags=['procurement','api','rate-limits']
3. mcp__artel__task_create — title='Implement bulk tender sync using /tenders/batch', description='Rate limit 100 req/min. Batch 500 ids. Must use cursor pagination.'
4. mcp__artel__session_handoff — summary='Researched procurement API limits. Filed task to implement bulk sync.', next_steps=['Implement bulk tender sync']

Do not add any text between tool calls. Call all four tools."

sleep 2.0

# ── ACT II ────────────────────────────────────────────────────────────────
_act 2 "SESSION TWO  (cold start)"

_claude "You are starting a fresh Claude Code session — cold start.
Use these Artel MCP tools in order:

1. mcp__artel__session_context — load context from the last session
2. mcp__artel__task_list — find open tasks (status='open'), look for the bulk sync task
3. mcp__artel__task_claim — claim the bulk tender sync task by its id
4. mcp__artel__memory_search — search for 'procurement API rate limits batch cursor'

Call all four tools. Do not add text between calls."

sleep 1.5

# ── Finale ────────────────────────────────────────────────────────────────
printf "\n  \033[1;97m╔══════════════════════════════════════════════╗\033[0m\n"
printf "  \033[1;97m║  \033[32mzero cold starts\033[97m                             ║\033[0m\n"
printf "  \033[1;97m║  \033[2mmemory · tasks · handoffs between sessions\033[97m   \033[0m\033[1;97m║\033[0m\n"
printf "  \033[1;97m║  \033[2mone line in .mcp.json — any agent, any host\033[97m  \033[0m\033[1;97m║\033[0m\n"
printf "  \033[1;97m╚══════════════════════════════════════════════╝\033[0m\n"
sleep 4.0
