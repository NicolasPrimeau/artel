#!/usr/bin/env python3
"""Measure what the Artel plugin costs a session: latency and injected context.

The README's "~10 ms, off the hot path" is true of the CAPTURE hook. It is not
true of the plugin as a whole — recall, inbox and gotcha all run on the hot path.
This script exists so that claim is a measurement rather than a memory.

    ARTEL_URL=... ARTEL_AGENT_ID=... ARTEL_API_KEY=... \\
      uv run python scripts/measure_hook_overhead.py

    --json     machine-readable
    --repeats  samples per hook (default 7)
    --fresh    new session id per call, defeating per-session dedup (worst case)

IMPORTANT: a hook with bad credentials returns nothing and says nothing. Zero
injected tokens means the plugin is inert, NOT that it is free — it still costs
its latency. The script fails loudly on that rather than reporting a happy zero.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

PROMPTS = [
    "fix the failing test in the auth middleware",
    "why does the archivist keep timing out during synthesis passes",
    "add a retry to the feed poller when the upstream returns 503",
    "refactor the memory search ranking to be less confusing",
    "what is the deployment story for this project",
]

# (label, script, fires-per, base payload)
HOOKS = [
    ("SessionStart", "artel-session-start.sh", "session", {"hook_event_name": "SessionStart"}),
    (
        "UserPromptSubmit:inbox",
        "artel-check-inbox.sh",
        "prompt",
        {"hook_event_name": "UserPromptSubmit"},
    ),
    (
        "UserPromptSubmit:recall",
        "artel-recall.sh",
        "prompt",
        {"hook_event_name": "UserPromptSubmit"},
    ),
    (
        "PreToolUse:gotcha",
        "artel-pretool-gotcha.sh",
        "tool call",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(ROOT / "artel" / "server" / "auth.py")},
        },
    ),
    ("Stop:capture", "artel-capture.sh", "turn", {"hook_event_name": "Stop"}),
]

# A session shape to price the per-event numbers into something meaningful.
SESSION_PROMPTS = 20
SESSION_TOOL_CALLS = 60
SESSION_DISTINCT_FILES = 15


def _tokens(text: str) -> int:
    """Crude but consistent: ~4 chars/token. Fine for comparing arms."""
    return round(len(text) / 4)


def _run(script: str, payload: dict) -> tuple[float, str]:
    start = time.perf_counter()
    proc = subprocess.run(
        [str(SCRIPTS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT)},
    )
    return (time.perf_counter() - start) * 1000, proc.stdout


def measure(repeats: int, fresh: bool) -> list[dict]:
    session = os.environ.get("CLAUDE_SESSION_ID", "overhead-bench")
    rows = []
    for label, script, fires, base in HOOKS:
        times, sizes = [], []
        for i in range(repeats):
            payload = dict(base)
            payload["session_id"] = f"{session}-{label}-{i}" if fresh else session
            payload["cwd"] = str(ROOT)
            if base["hook_event_name"] == "UserPromptSubmit":
                payload["prompt"] = PROMPTS[i % len(PROMPTS)]
            elapsed, out = _run(script, payload)
            times.append(elapsed)
            sizes.append(_tokens(out))
        ordered = sorted(times)
        rows.append(
            {
                "hook": label,
                "fires_per": fires,
                "p50_ms": round(statistics.median(ordered), 1),
                "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
                "max_ms": round(max(ordered), 1),
                "first_tokens": sizes[0],
                "steady_tokens": round(statistics.mean(sizes[1:])) if len(sizes) > 1 else sizes[0],
            }
        )
    return rows


def session_cost(rows: list[dict]) -> dict:
    by = {r["hook"]: r for r in rows}
    per_prompt_ms = by["UserPromptSubmit:inbox"]["p50_ms"] + by["UserPromptSubmit:recall"]["p50_ms"]
    latency = (
        by["SessionStart"]["p50_ms"]
        + SESSION_PROMPTS * per_prompt_ms
        + SESSION_TOOL_CALLS * by["PreToolUse:gotcha"]["p50_ms"]
        + SESSION_PROMPTS * by["Stop:capture"]["p50_ms"]
    )
    injected = (
        by["SessionStart"]["first_tokens"]
        + by["UserPromptSubmit:inbox"]["first_tokens"]
        + SESSION_PROMPTS * by["UserPromptSubmit:recall"]["steady_tokens"]
        + SESSION_DISTINCT_FILES * by["PreToolUse:gotcha"]["first_tokens"]
    )
    return {
        "assumed_prompts": SESSION_PROMPTS,
        "assumed_tool_calls": SESSION_TOOL_CALLS,
        "assumed_distinct_files": SESSION_DISTINCT_FILES,
        "added_latency_seconds": round(latency / 1000, 1),
        "injected_tokens": injected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--fresh", action="store_true", help="defeat dedup; worst case")
    args = parser.parse_args()

    rows = measure(args.repeats, args.fresh)
    total_injected = sum(r["first_tokens"] for r in rows)
    cost = session_cost(rows)

    if args.json:
        print(json.dumps({"hooks": rows, "session": cost, "inert": total_injected == 0}, indent=2))
    else:
        mode = "fresh session per call (dedup defeated)" if args.fresh else "one session (dedup on)"
        print(f"mode: {mode}   repeats: {args.repeats}\n")
        print(f"{'hook':26} {'per':10} {'p50 ms':>8} {'p95 ms':>8} {'first tok':>10} {'then':>6}")
        print("-" * 74)
        for r in rows:
            print(
                f"{r['hook']:26} {r['fires_per']:10} {r['p50_ms']:8.1f} {r['p95_ms']:8.1f}"
                f" {r['first_tokens']:10} {r['steady_tokens']:6}"
            )
        print("-" * 74)
        print(
            f"session of {cost['assumed_prompts']} prompts / {cost['assumed_tool_calls']} tool"
            f" calls: +{cost['added_latency_seconds']}s, +{cost['injected_tokens']} tokens"
        )

    if total_injected == 0:
        print(
            "\nFAIL: every hook injected nothing. The plugin is inert — almost certainly"
            "\nbad or missing credentials. It is still costing its latency. Do not report"
            "\nthis as 'no overhead', and do not benchmark against it: a treatment arm in"
            "\nthis state is a placebo.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
