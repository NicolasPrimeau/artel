---
title: Building a CRDT-replicated memory mesh for AI agents
published: false
description: How I gave a fleet of AI coding agents a shared brain — semantic memory, tasks, messages, session handoffs — that replicates peer-to-peer with no central coordinator and provably converges.
tags: ai, mcp, opensource, architecture
cover_image: https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docs/mesh_network5.gif
canonical_url:
---

Every multi-agent setup I tried ran into the same wall: the agents couldn't remember anything together.

Each Claude Code session started cold. Two agents working the same repo had no idea what the other had done. The "shared context" I kept building turned into a graveyard of half-finished orchestration scripts. Eventually I gave up on the orchestrators entirely and built the missing piece: a server that gives a fleet of agents a shared, semantic memory and coordination layer, with no central coordinator and no framework lock-in.

This post is about the part I think is most interesting: how the memory replicates between instances as a CRDT.

## The shape of the problem

A fleet of agents is not the same as a single agent with sub-agents.

- Sessions die. The next one needs to pick up where the last one left off, possibly on a different machine.
- Multiple agents on different hosts are doing related work. They shouldn't duplicate, shouldn't overwrite, shouldn't collide.
- There's no single process you can pin all the state to. A bot on my laptop, a bot in CI, and a bot on a colleague's machine should *all* be able to see the same memory.
- The agents aren't all the same framework. One is Claude Code. One is an AutoGen script. One is a cron job calling the API directly. They have nothing in common except HTTP.

The obvious answer — "put everything in a central database" — works until you need offline-ish behavior, or you don't want every team's data flowing through one shared server, or you want a peer-to-peer story between hosts.

What I wanted instead: each host runs its own server with its own SQLite database. Pairs of servers can *link*, and from then on their memory replicates in both directions, automatically. No quorum. No leader election. No central anything.

This is a classic CRDT problem.

![Two Artel instances meshing — memory written on one shows up on the other and vice versa, no central coordinator](https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docs/mesh_network5.gif)

## Why CRDTs, and which one

The constraints:

1. **No central coordinator.** Each instance writes locally, reads from peers asynchronously.
2. **Convergence.** Two instances that have seen the same set of operations must end in the same state.
3. **Idempotency.** Ingest the same entry twice → same result.
4. **No infinite loops.** A → B → A → B should terminate, not echo forever.

The data is a set of memory entries (think notes with content, tags, confidence, and a few metadata fields). Some get updated, some get tombstoned. There's no real "merge" semantics beyond last-write-wins per field.

I picked an **LWW-Element-Set CRDT** — formally proven convergent for exactly this kind of grow-and-tombstone data. The implementation has three pieces that matter:

**1. Entries are keyed by immutable origin UUID.** Every memory entry has an `id` minted at creation time, on the origin instance. When that entry replicates to a peer, the peer stores it with the *same* ID. No re-minting on ingest. This is what makes idempotency cheap — `INSERT OR REPLACE` is enough.

**2. The merge rule is `max(version)`, with `updated_at` as tiebreak.** Each entry has a monotonic `version` counter that the origin increments on every update. Peers compare `(version, updated_at)` tuples. Last-write-wins, but the version dominates so wall-clock skew between hosts almost never matters in practice. (Equal-version + skewed-clock is the one theoretical edge case; in three months of running this across machines I haven't hit it.)

**3. The origin guard prevents loops.** Every entry carries an `origin` field — the ID of the instance that minted it. When ingesting from a peer, we skip any entry whose origin matches our own. So if A pushes an entry to B, and B's feed echoes it back to A, A drops it. A→B→A terminates after one round-trip.

## The transport: just feeds

The replication protocol is *not* a custom binary thing. It's Atom and JSON Feed.

Each instance publishes its memory at two URLs:

```
GET /memory/feed.atom?mesh_token=...
GET /memory/feed.json?mesh_token=...
```

Both are normal RSS-style feeds, with one twist: the JSON Feed format includes a custom `_artel` extension on each item that carries the CRDT metadata (`origin`, `version`, `parents`, `scope`, `deleted_at`, etc.). This is what makes the merge work.

The mesh poller on each instance is just an RSS reader. It polls each linked peer's feed every N minutes, walks the new entries, applies the merge rule, writes to local SQLite. That's the entire replication loop. ~150 lines of Python.

The choice to use feeds instead of a custom protocol was deliberate:

- Any HTTP client can poll a feed. The mesh is debuggable with `curl`.
- Feeds are inherently pull-based — no need for either side to know the other's address ahead of time except for the initial link.
- The same feeds double as a public Atom/JSON Feed for human readers. The instance's UI can subscribe to its own feed to render a timeline.
- LAN peers discover each other via mDNS (`_artel._tcp.local.`). One click in the UI to link.

There's no fancy gossip protocol, no Merkle tree sync, no anti-entropy mechanism. Just polling feeds. It turns out that's enough for the throughput a fleet of LLMs generates — even an enthusiastic agent only writes a few entries per minute, far below what a feed poller can handle.

## SQLite + sqlite-vec for embeddings

The backing store is SQLite in WAL mode. For semantic search, [sqlite-vec](https://github.com/asg017/sqlite-vec) provides a virtual table for vector similarity. Embeddings are computed locally with a small model and inserted into a `memory_vec` table keyed by the same UUID as the main `memory` table.

```sql
CREATE VIRTUAL TABLE memory_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
```

Search is a join:

```sql
SELECT m.*, mv.distance
FROM memory_vec mv
JOIN memory m ON m.id = mv.id
WHERE mv.embedding MATCH ? AND k = ?
ORDER BY mv.distance;
```

Combined with conventional filters (tags, project, confidence floor, type), this is fast enough that I haven't needed to think about indexing strategy. The whole memory table is in WAL-mode SQLite; reads don't block writes.

When a CRDT merge happens — say a peer's version of an entry overwrites the local one — the embedding is re-computed and the `memory_vec` row is replaced. The semantic search index is always in sync with the merged content.

## The archivist: a background agent that keeps memory healthy

The other piece of the design I didn't expect to need is what I ended up calling the *archivist*: a long-running background process that watches all activity and does maintenance.

It does five things, on a loop:

1. **Synthesis.** Reads recent memory entries, looks for related ones, asks an LLM to write a connecting doc that summarizes the cluster. The connecting doc is just another memory entry, tagged appropriately, so it shows up in future searches.
2. **Dedup / merge.** When a new entry comes in that looks semantically very close to an existing one (cosine distance below a threshold), it tries to merge them. The merge is again handed to an LLM with both contents and a "produce a unified version" prompt.
3. **Decay.** Entries that haven't been re-written in a while have their `confidence` field lowered. Frequently-read entries are exempt. Below a floor, entries vanish from default searches (they're still there if you ask for low-confidence entries explicitly).
4. **Promotion.** Entries that have been stable, frequently-read, and confidence-high for long enough get promoted from `type="memory"` to `type="doc"` — which exempts them from decay and treats them as canonical reference material.
5. **Project briefs.** For each project, it maintains a short prose summary of "what's going on here" as a `doc`-typed entry tagged `project-brief`. The brief is surfaced automatically at the start of every agent session.

The archivist is just another agent. It has its own credentials, polls the event stream, makes API calls. There's nothing privileged about it from the server's perspective other than role-based access (the server enforces that the archivist can't *create* new projects from thin air — it can only touch projects that already have external presence).

This separation matters for two reasons. First, you can run an instance without an archivist if you don't want LLM-driven synthesis — the core memory store works fine on its own. Second, the archivist is *replaceable* — if you don't like the synthesis prompts or the decay heuristics, you can write your own.

## Identity and protocol: HTTP all the way down

Agents authenticate with an `agent_id` string and an API key. That's the entire identity model. No framework coupling, no SDK to install, no transport pinned. The server speaks plain HTTP + an MCP adapter on top.

A Claude Code session connects via an MCP plugin and sees the API as a set of tools (`memory_write`, `memory_search`, `task_claim`, `message_send`, etc.). An AutoGen script just calls `httpx.post("/memory", ...)`. A bash one-liner with `curl` participates in the fleet. The server doesn't know or care what's on the other end.

That decision came from frustration with how every multi-agent framework wants to be the *thing* — you build agents inside CrewAI, or inside LangGraph, or inside MemGPT, and they handle the coordination. Then you can't easily mix them, or use them alongside a hand-rolled script, or replace the framework later. I wanted the coordination layer to be the *opposite* — an HTTP server that knows nothing about the agents.

## What I'd do differently

A few honest postmortems.

**The version field.** I should have used a vector clock instead of a monotonic counter. The current scheme works because real-world version conflicts on the same entry from two different origins are rare. But "rare" isn't "never," and vector clocks would have made the edge case go away entirely.

**Embeddings are per-instance.** Each instance computes its own embeddings with its own model. Two instances with different models would have incompatible embeddings, and semantic search across the mesh would be broken. Right now this is an implicit contract — everyone uses the same default model. A proper fix would either share embeddings as part of the feed, or make the model an explicit per-instance setting that warns on mismatch.

**The archivist is a single point of opinion.** Decay rates, promotion thresholds, synthesis prompts — they're all hardcoded. They should be configurable per-instance, but right now changing them means editing Python. I'd make this more pluggable next time.

## Wrapping up

The whole thing is open source, MIT, and runs in one Docker container. It's been the spine of my own multi-agent setup for a few months now, and the bit I'm proudest of is that **it doesn't enforce a framework on anyone**. If your agent speaks HTTP, it's in the fleet.

The project is called Artel. The code is at [github.com/NicolasPrimeau/artel](https://github.com/NicolasPrimeau/artel) if you want to look — there's a live sandbox at artel.run (password `artel`) if you want to poke at the UI before installing anything.

I'd genuinely love feedback on the CRDT design, especially from anyone who's built distributed memory systems for LLMs and run into corners I haven't yet.

---

*Sidenote: the agent that helped me draft this post stored an earlier version as a memory entry in the live sandbox so other agents could read it. That's the idea.*
