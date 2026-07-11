#!/usr/bin/env bash
# Artel capture hook (Stop / PreCompact): spool the hook payload locally and fork a
# detached drainer. This is the ONLY thing on the agent's hot path, and it does no
# parsing and no network — just an append and a fork — so capture never slows Claude
# Code. The detached drainer (artel-drain.sh) compresses the session's new transcript
# slice and ships it to /captures off-path. The spool file is the durable WAL: if a
# drainer dies, the next capture hook's drainer picks up the accumulated payloads.
d="${ARTEL_SPOOL:-$HOME/.artel/spool}"
mkdir -p "$d" 2>/dev/null
{ cat; echo; } >> "$d/incoming.jsonl" 2>/dev/null
setsid "$(dirname "$0")/artel-drain.sh" >/dev/null 2>&1 </dev/null &
exit 0
