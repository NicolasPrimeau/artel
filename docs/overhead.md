---
anchors:
  - scripts/_artel_hooks.py
---

# What the plugin costs

The plugin buys ambient memory by spending latency and context. Until 2026-08-11 nobody had measured either — the README's "~10 ms, never on the agent's hot path" was carried forward as though it described the whole plugin, when it describes the **capture** hook alone.

Reproduce with:

```bash
uv run python scripts/measure_hook_overhead.py          # realistic, dedup on
uv run python scripts/measure_hook_overhead.py --fresh  # worst case, dedup defeated
uv run python scripts/measure_hook_overhead.py --json
```

## Measured

Against a local server, real fleet memory, real agent identity, 7 samples per hook, per-session dedup active:

| Hook | Fires | p50 | p95 | Tokens (first → then) |
| --- | --- | --- | --- | --- |
| `SessionStart` | per session | 696 ms | 729 ms | 612 → 612 |
| `UserPromptSubmit` · inbox | per prompt | 65 ms | 69 ms | 0 → 0 |
| `UserPromptSubmit` · recall | per prompt | 195 ms | 221 ms | 112 → 88 |
| `PreToolUse` · gotcha | **per tool call** | 189 ms | 204 ms | 123 → 0 |
| `Stop` · capture | per turn | 16 ms | 17 ms | 0 → 0 |

For a session of 20 prompts and 60 tool calls: **≈ 17.6 s of added wall-clock and ≈ 4,200 tokens of context.**

Two things worth reading off that table:

- **The dominant cost is `PreToolUse`**, because it fires on every tool call. At 189 ms it is most of the added wall-clock, and it is not the hook anyone would have guessed.
- **`Stop` · capture is genuinely cheap** — 16 ms, injects nothing. The original claim holds for the hook it was made about; it just never covered recall, inbox or gotcha.

Numbers to treat with care: this is `localhost`, so a remote instance is strictly worse; tokens are counted as characters/4; the inbox figure depends entirely on whether that agent has unread messages, and was 547 tokens for an identity that did.

## An inert plugin is not a free plugin

With bad credentials every hook returns nothing, logs nothing, and injects nothing. The same session then costs **6.6 s and 0 tokens** — all of the latency, none of the benefit, and no signal anywhere that it is happening.

`measure_hook_overhead.py` exits non-zero when total injection is zero, for exactly this reason. It is also a hazard for any A/B measurement: a treatment arm in this state is a placebo, and would produce a clean, entirely meaningless null result. Any benchmark comparing Artel on/off must assert non-zero injection while it runs, not merely check that the plugin is installed.
