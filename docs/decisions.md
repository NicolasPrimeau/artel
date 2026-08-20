<!-- covers: decisions -->
# Decisions

Notes decay, merge, and get rewritten by the archivist. That is right for knowledge and wrong for the record of what you chose and why: a decision that quietly changes later is worse than no record at all.

So decisions are a separate, **append-only** primitive. They are never merged, never decayed, never edited.

```bash
decision_write(decision="use SQLite, not Postgres",
               rationale="single-file backup and WAL are worth more than concurrent writers here",
               alternatives=["Postgres", "DuckDB"])
decision_list()      # what has been decided, newest first
decision_get(id)     # one decision in full
```

Each record carries the choice, the reasoning, the alternatives considered, who made it, and optionally the task it came out of. When someone asks six months later why the store is a single file, the answer is on the record with its alternatives — instead of being reconstructed, badly, from a merged note.
