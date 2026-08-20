<!-- covers: blueprints -->
# Blueprints

A `skill` note says how to do something. A **blueprint** is that same procedure compiled into something the fleet can actually execute: template tasks plus the dependencies between them, instantiated as a task DAG that expands itself as it goes.

```bash
blueprint_list()                                   # what's available
blueprint_instantiate("weekly-audit", {"repo": "artel"})   # start a run
blueprint_run(run_id)                              # where it got to
```

Instantiating materializes only the root wave. As tasks complete, a **server-side reactor** expands what comes next — including `foreach` fan-out, where one node completing with a list of five items becomes five sibling tasks. The shape of the run isn't known in advance; it's discovered while running.

**Completion contracts.** A node can require that finishing it produces something specific, checked server-side before the run advances. Three kinds:

| Check | What it verifies |
|---|---|
| `payload` | The completion body has the declared shape — required fields, array minimums. |
| `sqlite` | A query against the store returns what the node promised. |
| `git` | The repository actually changed. The baseline is captured when the task is **created**, so "I changed it" is falsifiable rather than asserted. |

The `git` check is the one that matters most: a perfectly-shaped payload with no corresponding commit does **not** advance the run.

**Lowering.** Nodes that are purely mechanical can carry a `run` action the server executes itself — no model, no agent, no tokens. `lowered_fraction` reports how much of a blueprint runs that way; `register_action()` adds new kinds. The goal is that agents are spent on judgement, not on plumbing.

**Where they come from.** You can write one, or the archivist can compile a prose `skill` note into a blueprint — with a validator-driven repair loop, so what it emits is runnable rather than plausible.
