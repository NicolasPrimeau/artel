---
description: "A written procedure compiled into steps the server verifies, surfaced to the agent one line at a time."
---

<!-- covers: blueprints -->
# Blueprints

A `skill` note says how to do something. A **blueprint** is that same procedure compiled into steps with dependencies between them and a check on each, so the server knows where a run stands without anyone reporting it. The agent doing the work sees one line: the current step and what makes it done.

```bash
blueprint_list()                                   # what's available
blueprint_instantiate("weekly-audit", {"repo": "artel"})   # start a run
blueprint_run(run_id)                              # where it got to
```

Instantiating materializes only the root wave. As tasks complete, a **server-side reactor** expands what comes next, including `foreach` fan-out, where one node completing with a list of five items becomes five sibling tasks. The shape of the run isn't known in advance; it's discovered while running.

**Completion contracts.** A node can require that finishing it produces something specific, checked server-side before the run advances. Three kinds:

| Check | What it verifies |
|---|---|
| `payload` | The completion body has the declared shape, required fields, array minimums. |
| `sqlite` | A query against the store returns what the node promised. |
| `git` | The repository actually changed. The baseline is captured when the run **starts**, before any of the work, so "I changed it" is falsifiable rather than asserted. |

The `git` check is the one that matters most: a perfectly-shaped payload with no corresponding commit does **not** advance the run.

**Steps that advance themselves.** A step whose check reads the repository or the store needs nobody to report it done. `GET /blueprints/steps` evaluates those checks first and completes every step the evidence already supports, so one commit that finishes three steps advances three, then returns what is still open. The plugin's prompt hook reads it and injects one line when the step is new to the session: the procedure, the position, the step, and what makes it done. The agent never claims or completes anything; it does the work and the next prompt carries the next step. A step with a completion contract or no check still waits for an agent, because nothing observable says it is finished. `MCP_BOARD_TOOLS=false` hides the task and message tools for fleets that run this way.

**Lowering.** Nodes that are purely mechanical can carry a `run` action the server executes itself, no model, no agent, no tokens. `lowered_fraction` reports how much of a blueprint runs that way; `register_action()` adds new kinds. The goal is that agents are spent on judgement, not on plumbing.

**Where they come from.** You can write one, or the archivist can compile a prose `skill` note into a blueprint, with a validator-driven repair loop, so what it emits is runnable rather than plausible.
