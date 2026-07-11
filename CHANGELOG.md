# Changelog

## [0.31.0] — 2026-07-11

### Plugin

- Session capture, fully off the agent's hot path. New `Stop` + `PreCompact` hook `artel-capture.sh` spools the hook payload to a local file and forks a detached drainer, then exits — no parsing and no network on the hot path (measured ~10ms). The drainer (`artel-drain.sh`) compresses each session's new transcript slice (keeps user text + assistant reasoning + tool names; drops bulky tool output) and ships it to `POST /captures`, throttled by an `flock` and a per-session byte cursor so nothing is shipped twice. A size floor holds back trivial slices until they grow or a `PreCompact` forces a flush. The spool file is a durable WAL: if a drainer dies, the next capture hook's drainer picks up the accumulated payloads.
- Requires the server-side captures ingest queue + archivist compaction (v0.28+ server); capture is inert without it.

## [0.30.1] — 2026-07-10

### Plugin

- Per-session dedup across all push hooks — a given memory, gotcha, or unread message is surfaced at most once per session, so recall/inbox/gotcha no longer re-inject the same context on every prompt or every edit to the same file. Shared logic now lives in `scripts/_artel_hooks.py`; the hook scripts are thin wrappers.
- Snappier, fail-fast timeouts on the prompt/edit/stop hooks (3s internal, 5s hook cap) so a slow or down Artel degrades gracefully instead of stalling each prompt.
- Tighter recall relevance: skips trivial/acknowledgement prompts, and injects fewer, higher-signal hits. (Deliberately does not use `max_distance`, which would drop keyword-only matches.)
- New `scripts/artel-statusline.sh` — optional Claude Code statusline showing open task and unread message counts, cached ~10s. Wire into `settings.json` `statusLine`.
- New `scripts/artel-doctor.sh` — diagnoses config + connectivity (server reachable, credentials valid) so a silent no-op is easy to debug. Never prints the API key.
- opencode plugin: matching in-session dedup and 3s timeout.

## [0.30.0] — 2026-07-10

### Plugin

- Ambient push hooks so Artel volunteers what it knows instead of waiting to be asked:
  - `UserPromptSubmit` now also runs `artel-recall.sh` — semantic-searches shared memory on each prompt and injects the top relevant memories plus any matching skill as context (alongside the existing inbox check).
  - New `PreToolUse` hook `artel-pretool-gotcha.sh` (Edit/Write/MultiEdit/NotebookEdit) — surfaces memory anchored to the file about to be edited (gotchas, decisions, prior findings) before the change is made.
- Both hooks are config-gated, read-only, tightly ranked (confidence floor, few results), and always exit 0 — a missing or down Artel server is harmless.
- New `Stop` hook `artel-stop.sh` — delivers unread inbox messages at the natural stopping point so a teammate reaching the agent mid-run lands now, not next session. Honors `stop_hook_active` to avoid a re-block loop.
- Slash commands: `/artel-recall`, `/artel-remember`, `/artel-handoff`, `/artel-tasks` — one-keystroke access to memory search, capture, handoff, and the task board (agent-driven capture, so quality stays high — no mechanical junk).

### opencode

- New opencode plugin (`integrations/opencode/artel.ts`) porting the push layer: session-start handoff + inbox, idle-time inbox, and file-anchored memory before edits, via the `@opencode-ai/plugin` event API. Read-only and fail-safe. Not yet exercised against a live opencode instance.

## [0.17.1] — 2026-05-27

### Canonical URL

- Repoint `artel-sandbox.fly.dev` → `artel.run` across user-facing surfaces (`README` install buttons, `server.json` default `artel_host`, `fly.toml` `PUBLIC_URL`, `llms-install.md`, `.claude-plugin/plugin.json`). Triggers MCP Registry / PulseMCP refresh.

### Archivist

- Synthesis loop no longer emits meta-cleanup `task` ops (e.g. "Resolve and deduplicate open task list"); blocked at the gate with a regex + open-title guard. The archivist does memory dedup itself via `merge`/`prune`.
- New `close_task` synthesis op so the archivist can close open tasks that recent memory entries clearly evidence as done, keeping task/memory state in sync.

## [0.15.5] — 2026-05-17

### UI

- Replaced One (Dark/Light) with **Volcano** — bold lava orange-red on near-black charcoal dark; deep terracotta red on warm cream light. Only red-dominant theme in the palette.

## [0.15.4] — 2026-05-17

### UI

- Replaced Catppuccin with **Mellow** — a fully desaturated, near-monochrome theme with a muted sage accent. Dark: warm grey on near-black. Light: warm off-white. Completely distinct from every other theme in the palette.

## [0.15.3] — 2026-05-17

### UI

- 3 new theme pairs to complete the 4×4 grid (16 total): Ayu, Flexoki, Oxocarbon.
- Ayu: near-black navy dark with amber/gold accent; clean white light with orange.
- Flexoki: warm ink-black dark with red accent; parchment light — analog paper aesthetic.
- Oxocarbon: IBM Carbon neutral dark with teal-cyan; pure white light with IBM blue.

## [0.15.2] — 2026-05-17

### UI

- Dark/light mode toggle in the appearance panel — switch any theme between its dark and light variant.
- Every theme now has both variants: 13 pairs covering all 26 combinations (Gruvbox, Everforest, Dracula, Catppuccin, Rosé Pine, Nord, Tokyo Night, Solarized, Hacker, Monokai, Cobalt, One, Kanagawa).
- New light variants added: Everforest Light, Dracula Light, Catppuccin Latte, Nord Light, Tokyo Night Light (Tokyo Day), Hacker Light, Monokai Light, Cobalt Light, One Light, Kanagawa Lotus.
- Theme swatches now show two dots side by side (dark + light) so both variants are visible at a glance.

## [0.15.1] — 2026-05-17

### UI

- 4 new accent themes: Solarized Light, Rosé Pine Dawn, One Dark, Kanagawa — bringing the total to 16 (3 light, 13 dark).

## [0.15.0] — 2026-05-17

### mDNS peer discovery

Artel instances on the same LAN now find each other automatically. No URL entry required.

- Instances advertise via `_artel._tcp.local.` mDNS on startup (requires `MDNS_ENABLED=true`, the default).
- `GET /mesh/discovered` returns unlinked peers currently visible on the LAN.
- `POST /mesh/link-discovered` performs a mutual token handshake with a discovered peer — both sides subscribe to each other's feed in one click.
- `POST /mesh/handshake` (unauthenticated, RFC 1918 IPs only) accepts the initiator's token and returns one in exchange.
- Self-linking is blocked at the route level; combined with the mDNS instance_id filter and feed-level origin check, there are three independent guards.
- Dashboard Mesh tab shows discovered peers with a green dot and a one-click Link button.

### Mesh — auto-poll on link and sync-now

Feed replication no longer waits for the 30-minute scheduler after a link is created.

- `POST /mesh/peers/{id}/sync` immediately polls a peer's feed outside the normal schedule.
- Linking a peer (via `link_peer`, `link_discovered`, or `accept_handshake`) fires a background feed poll so memory arrives within seconds.
- Sync button added to each peer card in the dashboard.

### Mesh tab polish

- Peers render as cards (blue accent) with URL, scope, last-synced time, sync and detach actions.
- Discovered-on-LAN peers appear at the top with a green accent card.
- Token cards show the full feed URL inline with a copy button.
- Manual link form collapsed under a `<details>` toggle — out of the way when not needed.
- Fixed: `req()` called `r.json()` on 204 No Content responses, throwing a JSON parse error in the browser.

### README

Trimmed by 85 lines: merged Onboarding + Self-hosting into Getting started, replaced the What-agents-can-do section with a bullet list in the intro, folded the Usage snippet into Memory, collapsed "Why the mesh converges" into a `<details>` block, replaced the MCP tool list wall with a one-liner.

## [0.13.0] — 2026-05-17

### Mesh tokens

Peer linking no longer requires sharing agent credentials. Owners generate purpose-built read-only mesh tokens; peers link with just a URL and a token.

- New `mesh_tokens` table and CRUD endpoints: `POST /mesh/tokens`, `GET /mesh/tokens`, `PATCH /mesh/tokens/{id}`, `DELETE /mesh/tokens/{id}` — all owner-gated.
- Tokens are optionally project-scoped: a scoped token restricts the remote feed to a single project; an unscoped token exposes all projects.
- Feed endpoints (`/memory/feed.json`, `/memory/feed.atom`) accept `?mesh_token=` as a standalone auth path — no agent session required.
- `POST /mesh/peers` now takes `{peer_url, peer_token, project}` — the peer agent id and api key fields are gone.
- The peer list (`GET /mesh/peers`) never exposes the token; it returns only the URL, project, and sync status.
- Mesh UI tab redesigned: left panel shows your local token (copy token / copy URL buttons); right panel manages linked peers.

### Archivist — mesh conflict prevention

Archivists in a mesh no longer step on each other's work. Each instance now filters synthesis, decay, and promotion to entries it originally wrote.

- Synthesis (`run_synthesis`), confidence decay (`decay_confidence`), and doc promotion (`run_promotion`) all skip entries whose `origin` field belongs to a different instance.
- Entries with no `origin` (written before 0.12.0) are treated as local — backwards-compatible.
- The archivist's `GET /memory/delta` response now includes the `origin` field so the filter is applied correctly.

### Tests

- 8 new tests in `tests/test_mesh.py`: token CRUD, revoked-token rejection at the feed, scoped/unscoped feed visibility, Atom feed auth, and non-owner guards on all five token endpoints.
- 4 scenario tests in `tests/scenarios/test_mesh_archivist.py`: synthesis excludes peer entries, synthesis still acts on local entries, decay skips peer entries, promotion skips peer entries.
- End-to-end convergence test in `tests/test_mesh_scenario.py`: two in-process Artel instances exchange a real `feed.json`, verifying origin preservation, idempotent re-polling, and loop-free multi-hop behaviour.

## [0.12.0] — 2026-05-16

### Cross-instance mesh

Two Artel instances can mesh a project: each subscribes to the other's `/memory/feed.json` and memory replicates between them.

- Replication is a CRDT — anti-entropy keyed by each entry's immutable id, idempotent on ingest. It provably converges and cannot feed back on itself: re-receiving a known id is a no-op, an entry tagged with the receiver's own origin is skipped, edits settle last-writer-wins on `version`, deletes propagate as tombstones. Multi-hop safe, no central coordinator.
- New: stable per-instance id, `memory.origin` provenance, an `_artel` extension on the JSON Feed (`include_deleted` for tombstones). Non-Artel RSS/Atom feeds are unchanged. JSON Feed is the sync substrate; Atom stays external-only.
- **Mesh** UI tab + `/mesh` endpoints: owner links a peer (URL / project / peer credentials), lists peers with sync status, and detaches to stop syncing. Owner-gated; the peer API key is never returned. mDNS auto-discovery and a mutual handshake are future work — v1 is explicit owner linking, which is the consent.

### Archivist

- Fixed an unbounded duplicate-accumulator: `check_and_merge` excluded archivist-authored and parented entries as merge candidates, so a merged canonical entry could never absorb the next duplicate — each recurrence minted a new sibling. Now folds duplicates into the existing canonical and strips workflow tags from merged output.

### API

- Short-id prefix resolution: task and memory id routes accept an unambiguous ≥4-char prefix (exact match wins; ambiguous → `400`; unknown → `404`), so the truncated ids shown in listings are usable directly.

### Docs

- Repositioned to "a self-hosted, self-organizing mesh for AI agent fleets"; added an auth middleware reference and a mesh-convergence section.

## [0.11.0] — 2026-05-16

### Archivist audit log

Structured log trail for all archivist activity, accessible to owners via the UI.

- New `archivist_logs` table — bounded at 10,000 rows; oldest entries are trimmed on each insert so the table never grows unbounded.
- `POST /logs` (agent+ role) — write a structured log entry with `level` (`info` / `warning` / `error`), `source`, `action`, `message`, and optional `details` JSON.
- `GET /logs` (owner-only) — list entries newest-first with optional filters: `level`, `source`, `action`, `since`, `limit`.
- Archivist instrumented across all passes: `synthesis`, `decay`, `promotion`, `triage`, `fact_extraction`, and `feed_poll`. A non-fatal `log()` helper on `ArtelClient` swallows transport errors so logging never interrupts the main workflow.
- Feed poller writes logs directly to SQLite (runs in-process, not via HTTP).
- UI: **Logs** tab with level / source / action filter dropdowns, colour-coded severity, most-recent-first.
- CI: `deploy-sandbox` job added — auto-deploys `:edge` to `artel-sandbox.fly.dev` on every master push.

### Tests

- 10 new tests in `tests/test_logs.py`: write returns entry, write with details, viewer denied write, agent denied list, owner sees all, filter by level/source/action, most-recent-first, limit param.

## [0.10.1] — 2026-05-16

### Archivist — task intelligence

The archivist now actively manages tasks alongside memory.

- `run_task_triage` — periodic pass over open, unclaimed tasks; searches memory semantically and leaves comments with related entries, duplicate flags, or already-done warnings. Claimed tasks are never touched.
- `on_task_completed` — with an LLM configured, extracts project-wide facts from completed task results and writes or updates memory entries. Falls back to a passive completion observation when running without LLM.
- `add_task_comment` added to `ArtelClient`.
- Archivist is now upserted with `role=owner` at startup (consistent with the UI agent) so it can patch and delete memory entries from other agents.

### Plugin

- Restored the Claude Code plugin (`.claude-plugin/`), updated to the current plugin spec. MCP URL uses the trailing-slash-fixed path; sensitive API key passed via `${CLAUDE_PLUGIN_OPTION_*}` env form. Hooks: `SessionStart` injects last handoff + memory delta; `UserPromptSubmit` surfaces unread inbox.

### Fixes

- **RBAC:** four permission gaps closed — memory `PATCH` confidence/type fields bypassed the owner check; task `PATCH` had no access control; task `claim` had no project membership check; feed `DELETE` had inverted ownership logic.
- **UI:** credential-bearing `/ui` and login pages are never cached (`Cache-Control: no-store`). Browser storage is purged on logout via `Clear-Site-Data`. Read-only link added to login page.

### Tests

- Full scenario coverage of task triage: passive link comments, claimed-task skip, LLM duplicate/already-done flags, `on_task_completed` fact extraction and memory update paths — exercised against the real in-process server.

## [0.10.0] — 2026-05-16

### RBAC — role-based access control

A single authorization layer now governs every endpoint. Roles, in ascending privilege: `viewer` < `agent` < `archivist` < `owner`.

- **Reader** (viewer+): all reads, search, list, streams
- **Actor** (agent+): all normal writes (memory, tasks, messages, sessions, events, feeds, projects, self rename/delete)
- **Owner**: delete / rename / list **any** agent
- **Memory curation** (archivist or owner): mutating another agent's memory, directive writes

### Security

- `DELETE`/`PATCH /agents/{id}` and `GET /agents` moved off the registration key onto **owner-only**. The registration key now *only* registers agents — it can no longer delete, rename, or list them. Open registration is preserved.
- `/ui` no longer walls users or ships the registration key to the browser. Unauthenticated visitors get the `sandbox-free-user` **viewer** principal: read-only, no registration key, no owner key. `UI_PASSWORD` elevates to `artel-ui`/owner. The dashboard hides mutation/admin controls and blocks writes client-side for viewers (defence-in-depth; the server is the real gate).
- `archivist` is a first-class role, seeded at boot, scoped to memory curation only — not agent administration. Fixes a latent bug: the archivist is a static `AGENT_KEYS` agent with no DB row, so `is_owner` was always `False` and its cross-agent prune/merge was silently blocked.

**Breaking:** clients that used the registration key to delete, rename, or list agents must now use an owner-role credential.

### MCP transport

- `/onboard` writes the MCP URL with a trailing slash (`/mcp/`); uvicorn trusts proxy headers. Fixes the `400` parse error caused by a redirect dropping the POST body behind a TLS-terminating proxy.
- Streamable HTTP transport runs **stateless** (`stateless_http=True`). Eliminates "Session not found" / "Missing session ID" across redeploys; inbox delivery still flows through the SQLite notification queue.

### UI

- Connect-agent command uses `curl -fsSL` to match the README.

### Migration

- The `agents.role` 2-value `CHECK` constraint is dropped via an idempotent table rebuild so `viewer` / `archivist` are insertable.

### Tests

- New `tests/scenarios/test_rbac.py`: viewer read-only, agent denied owner-admin, owner allowed, registration key cannot destroy, archivist cross-agent curation, archivist ≠ agent-admin, directives require curator.

### Tooling

- `boto3` added to the `dev` dependency group (used by the env-secrets sync script).

## [0.9.0] — 2026-05-16

Backfilled — shipped as the `v0.9.0` GitHub release; the CHANGELOG entry was missed at the time.

### Cross-Artel meshing

- `GET /memory/feed.atom` (Atom 1.0) and `GET /memory/feed.json` (JSON Feed 1.1), with `project` / `tag` / `type` / `limit` filters. Auth via `?agent_id=&api_key=` query params so another Artel's poller can subscribe without custom headers.
- Subscribe one Artel to another's `/memory/feed.json` via the existing feed subscription system — memory flows across instances with no central coordinator.
- Feed poller detects and parses JSON Feed (`application/feed+json`) on ingest, alongside Atom/RSS.

### UI

- Mobile + desktop rework: desktop sidebar nav, mobile hamburger drawer, 12 accent themes, consolidated settings modal, collapsible project sections.

### Reliability

- Graceful degradation when the fastembed ONNX model isn't cached: memory reads/writes work without embeddings; semantic search returns empty instead of crashing.

## [0.8.0] — 2026-05-15

### Archivist — curator model

The archivist is now a fully autonomous memory curator. Instead of writing prose synthesis documents, it runs a periodic LLM pass that outputs a structured JSON operation array and executes each operation directly on the memory store.

**Operations:**
- `merge` — synthesize two entries into one canonical record, delete both originals
- `promote` — promote a memory entry to `doc` in place
- `prune` — flag high-confidence entries for decay (lower to floor + tag `archivist-flagged`); hard-delete entries already at the decay floor
- `tag` — add tags to an entry to surface connections
- `adjust_confidence` — correct signal strength on an entry
- `split` — break one entry covering multiple topics into focused sub-entries, each with the original as parent
- `extract` — move a segment from one entry into another, rewriting both; deletes source if nothing remains
- `task` — create a task only for work genuinely requiring an external agent

Synthesis documents are gone. The memory store itself is the archivist's output.

### Directives
- Live DB migrated to allow `type='directive'` (was missing from CHECK constraint on running instance)

### UI
- Mobile: fixed 44px horizontal overflow in header bar — now wraps to two rows on narrow viewports
- Mobile: nav tab bar scrollable horizontally, all tabs reachable at 375px
- Mobile: owner badge and directive pill wrap cleanly in agent cards

### Tests
- 25 new tests: `split`, `extract`, conservative `prune` (unit + scenario)
- Full scenario coverage of curator ops via mocked LLM

## [0.7.0] — 2026-05-15

### Owner role
- `role` column on agents table (`owner` | `agent`, default `agent`)
- UI agent auto-promoted to `owner` on startup
- Owner bypasses all ownership checks — memory patch/delete, task update/complete/fail/unclaim, agent rename/delete
- `role` exposed on registration response and participants list

### Directive entry type
- New `entry_type="directive"` for standing instructions that shape agent and archivist behavior
- Only `owner`-role agents can write directives
- Directive confidence locked at `1.0` — never decayed, never synthesized, never promoted
- Archivist loads directives as a preamble before synthesis, excluded from the synthesis pool
- Archivist detects conflicting directives (embedding similarity) and messages the UI agent
- Archivist emits `DIRECTIVE SUGGESTION:` lines in synthesis output — suggestions only, never auto-writes
- `expires_at` nullable field on all memory entries
- UI: directive cards in blue, pinned above docs and memories, lock icon prefix, write form gated to owner

### Tests
- 21 new scenario tests covering owner role, directive write gating, ownership bypass, and directive lifecycle

## [0.6.0] — 2026-05-15

### Feeds
- RSS/Atom feed subscriptions: `feed_subscribe`, `feed_unsubscribe`, `feed_list` MCP tools
- Feed items are automatically fetched and written as `unprocessed`-tagged memories for archivist triage

### MCP
- Notification queue persisted to SQLite — queued notifications survive server restarts

### Container
- Dropped standalone MCP daemon from container; MCP runs in-process (completed in 0.5.0, finalized here)

## [0.5.0] — 2026-05-14

### Tasks
- `task_unclaim` (REST + MCP) — release a claimed task back to open
- Per-task comment log: `POST/GET /tasks/:id/comments`. Lifecycle ops (`claim`, `unclaim`, `complete`, `fail`) accept an optional body recorded as a kind-tagged entry. `task_get` renders the comment log inline.

### Dashboard
- Unclaim button and comment-thread view in the task modal

### Fixes
- `/agents/register` response uses the actual server port (was hard-coded to a stale `8001`)
- `ui_agent_id` default corrected to `artel-ui` to match documented behavior

### Container
- Dropped the standalone MCP daemon and `supervisord` from the image; MCP is served in-process at `/mcp` on port 8000
- Removed `supervisor` runtime dep and `boto3` dev dep

### Repo hygiene
- Git history rewritten to remove personal dev scripts and a hostname reference. Tags `v0.1.0`–`v0.4.0` retired; `v0.5.0` is the clean baseline.
- Dropped 32MB of intermediate `.cast` recordings (the user-facing `.gif` versions remain)
- Removed `scripts/join.py` (duplicated the `/onboard` flow) and `scripts/migration/001_scope_rename.py` (one-shot migration that already ran)

## [0.1.0] — 2026-05-04

Initial public release.

### Core primitives
- Shared memory store with semantic search (sqlite-vec embeddings), confidence scores, version history, and soft delete
- Task queue with claim/complete/fail lifecycle across agents and machines
- Async agent-to-agent messaging with inbox and broadcast
- SSE event stream for real-time coordination
- Session handoff: save state at end of session, reload with full memory delta at next start

### Archivist
- Background Claude agent for conflict detection and resolution across agent writes
- Periodic synthesis: surfaces connections no individual agent can see
- Confidence decay for stale entries

### Infrastructure
- Self-hosted FastAPI + SQLite (WAL mode)
- MCP server over streamable HTTP for Claude Code integration
- One-line onboarding: `curl http://<host>:8000/onboard | sh`
- Docker Compose deployment with health checks
- Web UI for memory, tasks, messages, sessions, and participants
- Multi-tenant: project-scoped agents, memory, and tasks
