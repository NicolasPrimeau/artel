<!-- covers: ledger -->
# The ledger

What the fleet did, what it was worth, and what was decided — on its own port,
served by the same container.

```
http://<host>:8090
```

Separate from `/ui` deliberately. The audience is whoever owns the budget rather than
whoever operates the fleet, and keeping it apart means the money view stays read-only
and free of agent controls.

## What it shows

| | |
|---|---|
| **Accounts** | Per project: sessions, turns, output tokens, cache reads, decisions, value |
| **By session** | The unit people recognise — "that run cost this much" |
| **By decision** | What it cost to reach each recorded choice |

Every figure is computed from `usage_events`, `decisions` and `memory`. Nothing is
generated, and the page says so, because a spend report that guesses is worse than no
report.

## Repeated by hand

The panel that answers "what should we automate?" — mined from what the fleet wrote
about its own work, not from a model's opinion. Sentences matching `manually`,
`by hand`, `every time`, `must be re-run` and similar, grouped into themes and each
quoting the note it came from.

Deterministic on purpose. Ask a model what to automate and it will produce plausible
suggestions whether or not the evidence exists, and an unfalsifiable answer is worth
nothing to someone deciding where to spend a week. Every candidate here is a sentence
somebody actually wrote, with the entry id beside it.

The pattern is deliberately narrow. `must be re-\w+` once matched "must be resolved",
which is not repeated work — a false candidate is worse than a missed one, because it
sends someone to automate something that was never manual.

## Cost per decision is attribution, not causation

A decision carries the session it was mined from, and that session's tokens are what
it took to reach the choice. But a session that produced three decisions spent its
tokens on all three and on everything else it did that hour.

So the figure is **the cost of the work containing the decision**, and it is not
divided between the decisions in a session. Dividing would invent a precision the
data has not got. The UI states this under the list rather than leaving it implied.

## No git, no transcripts

The ledger runs inside the container, where neither exists. That is a constraint
worth keeping: a view that needed the repos checked out beside it would only work on
the machine that happened to hold them, which is not a product. Everything here comes
from the store.

## Rounding

Money is stored and returned at six decimal places, not two. Rounding to cents in the
data layer silently zeroes anything under half a cent — most single calls on a cheap
model — so display rounds and storage does not.
