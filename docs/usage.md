<!-- covers: usage -->
# Usage and cost

What the fleet spent, and what it is honest to say about it.

Artel records a **usage rollup** per session and model: turns, input, output, and the
two cache counters. Agents post it themselves — the capture hook already parses the
transcript for prose, so the numbers come from the same read.

```bash
POST /usage   {"model": "claude-opus-5", "session_id": "…", "billing_mode": "subscription",
               "turns": 412, "input_tokens": 8231, "output_tokens": 380122,
               "cache_read": 190337221, "cache_write": 2210945}

GET  /usage?days=30            # grouped by project and model, with cost where it exists
```

## Cost is computed, never estimated

A spend report that guesses is worse than no report, so `cost_usd` returns **no
number** rather than a plausible one. Three outcomes:

| Situation | Result |
|---|---|
| Metered model with a known rate | An amount, plus the `derivation` string that produced it |
| `billing_mode: subscription` | `null` — the tokens were spent but nobody is billed per token, so a dollar figure would be fiction |
| Model with no published rate | `null`, naming the model — `$0` would read as "this was free" |

Rates come from `MODEL_RATES` (a JSON map you supply) or live from OpenRouter's model
list when `OPENROUTER_API_KEY` is set, cached for six hours. There are no rates
compiled into the source, because prices change and a stale constant is a wrong
invoice.

`GET /usage` reports `billable_usd` and a separate `not_priced` list. It never adds
them together: mixing metered dollars with subscription volume produces a total
nobody is billed for.

## Declaring how you are billed

`ARTEL_BILLING_MODE` (`metered` or `subscription`) tells the drainer which it is. The
hook falls back to looking for an API key or an OAuth token, but a Claude Code session
reads its credentials from `~/.claude` rather than the environment, so detection
usually lands on `unknown` — and `unknown` is never priced. Set it once per host.

## Why cache counters matter more than they look

On a real fleet the cache read counter dominates everything else — commonly by three
orders of magnitude over fresh input. Any instinct to cut spend by switching to a
cheaper model is aimed at a rounding error; the lever is cache behaviour. Reporting
the four counters separately is what makes that visible.

## Double counting

`usage_events` carries a unique index on `(session_id, model, window_end)` and the
route inserts with `OR IGNORE`. A drainer that re-reads a transcript from an earlier
cursor will re-post windows it has already sent; billing them twice is the one error
this data must never make.
