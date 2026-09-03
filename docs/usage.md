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

Cost is always computed where a rate exists — the tokens are identical whether you
are on a seat or metered, and on a seat the list-price figure is the more interesting
one: it is what the work would have cost per-token, i.e. what the seat is worth.

`billing_mode` labels the number rather than withholding it:

| Mode | `billed` | `basis` |
|---|---|---|
| `metered` | `true` | actual spend |
| anything else | `false` | list-price equivalent |

`GET /usage` returns `billed_usd` and `list_equivalent_usd` separately. They are never
added: one is an invoice, the other is a valuation.

There is still no number when there is no rate. `$0` reads as "this was free", the
most expensive way for a spend report to be wrong.

Rates come from `MODEL_RATES` or live from OpenRouter, cached six hours, with none
compiled in — prices move and a stale constant is a wrong invoice. Model ids are
normalised on lookup: a session records `claude-opus-4-8` while rate tables name it
`anthropic/claude-opus-4.8`, and without that every Anthropic model reads as unpriced.

## Declaring how you are billed

`ARTEL_BILLING_MODE` (`metered` or `subscription`) tells the drainer which it is. It
only changes the label — an unset host still gets figures, reported as list-price
equivalents rather than as an invoice.

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
