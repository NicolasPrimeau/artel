<!-- covers: captures -->
# Capture

The best notes are the ones you never got around to writing. Capture is the pad writing them for you: what happened in a session becomes durable notes, without slowing anything down and without dumping raw noise into the pad.

The rest of this section is how that's kept honest.

**A two-tier write.** Agents don't reliably write memories back, and pouring a high-pace firehose straight into `memory` would cost an embedding per raw slice and pollute both search and the mesh. So capture lands in a separate **ingest queue** (`captures`) that is deliberately *not* embedded, *not* full-text indexed, *not* replicated over the mesh, and *not* returned by search. Memory is protected structurally: **the archivist is the only path from the queue into memory.**

**Off the hot path.** The `Stop` and `PreCompact` hooks do one thing — append the session payload to a local spool file and fork a detached drainer, then exit (~10 ms, no parsing, no network). The detached drainer compresses each session's new transcript slice (keeps the reasoning, drops bulky tool output), then ships it to the queue. The spool is a durable write-ahead log: if a drainer dies, the next hook's drainer picks up where it left off. Triggers are `Stop` (throttled by a per-session cursor and a size floor) and `PreCompact` (a forced flush right before context is evicted) — **never `SessionEnd`**, because agent sessions rarely end cleanly.

**Leveled compaction (LSM-style).** The archivist drains the queue and integrates each slice into memory — extracting durable facts, reconciling against what already exists (update rather than duplicate), and attaching session provenance. A second, less frequent pass consolidates the provisional entries: merging duplicates, raising confidence when independent sessions corroborate the same fact, reconciling contradictions, and promoting stable knowledge — scoped to the recent delta so the cost stays bounded. Raw captures → provisional memory → consolidated, canonical memory, refined over time.

The net effect: memory quality is decoupled from write volume. Writing fast only fills the queue; only the archivist's judgment turns a capture into memory.
