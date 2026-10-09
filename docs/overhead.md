---
description: "What the plugin actually costs per session and per tool call, measured rather than estimated."
anchors:
  - scripts/_artel_hooks.py
---

# What the plugin costs

The plugin buys ambient memory by spending latency and context. Until 2026-08-11 nobody had measured either, the README's "~10 ms, never on the agent's hot path" was carried forward as though it described the whole plugin, when it describes the **capture** hook alone.

Reproduce with:

```bash
uv run python scripts/measure_hook_overhead.py          # realistic, dedup on
uv run python scripts/measure_hook_overhead.py --fresh  # worst case, dedup defeated
uv run python scripts/measure_hook_overhead.py --json
```

## Measured

Against a local server, real fleet memory, real agent identity, 7 samples per hook, per-session dedup active, re-measured 2026-10-09:

| Hook | Fires | p50 | p95 | Tokens (first → then) |
| --- | --- | --- | --- | --- |
| `SessionStart` | per session | 171 ms | 176 ms | 88 → 88 |
| `UserPromptSubmit` · recall | per prompt | 219 ms | 233 ms | 69 → 41 |
| `PreToolUse` · gotcha | **per tool call** | 176 ms | 453 ms | 72 → 0 |
| `Stop` · capture | per turn | 18 ms | 20 ms | 0 → 0 |

For a session of 20 prompts and 60 tool calls: **≈ 15.4 s of added wall-clock and ≈ 2,000 tokens of context.**

**The dominant cost is `PreToolUse`**, because it fires on every tool call. At 176 ms it is most of the added wall-clock, and it is not the hook anyone would have guessed. Capture stays cheap because its real work, including the token rollup posted to `/usage`, happens in a detached drainer whose own cost is not in this table.

## Injections are budgeted

The first measurement, on 2026-08-11, came to 17.6 s and 4,200 tokens, and most of the tokens were waste: session start injected the whole last handoff (612 tokens in that run, about 1,250 in a later real session), and recall cut notes at a fixed character count, often mid-word. An injection is a nudge, so each one now has a hard budget. Session start is a pointer to the handoff of at most 600 characters, recall and file notes are at most 480, each line is one headline or one sentence cut at a sentence or word boundary, and a line that does not fit is dropped whole. `tests/test_hook_injection_budget.py` holds every hook to that.

Numbers to treat with care: this is `localhost`, so a remote instance is strictly worse, and tokens are counted as characters/4.

## An inert plugin is not a free plugin

With bad credentials every hook returns nothing, logs nothing, and injects nothing. The same session then costs **6.6 s and 0 tokens**, all of the latency, none of the benefit, and no signal anywhere that it is happening.

`measure_hook_overhead.py` exits non-zero when total injection is zero, for exactly this reason. It is also a hazard for any A/B measurement: a treatment arm in this state is a placebo, and would produce a clean, entirely meaningless null result. Any benchmark comparing Artel on/off must assert non-zero injection while it runs, not merely check that the plugin is installed.
