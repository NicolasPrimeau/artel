<!-- covers: archivist -->
# Archivist

This is the part that makes the pad learn, and the only writer allowed to turn raw captures into notes. It is optional: without it you get a pad that remembers rather than one that improves.

**With an LLM configured**, it drains the capture queue into clean, deduplicated, provenance-tagged notes (minor pass), then consolidates them over time — merging duplicates, raising confidence when independent sessions corroborate, resolving contradictions, and promoting what proves stable (major pass). It also detects semantic conflicts on write and synthesizes cross-agent findings into shared `doc` entries.

**Without LLM (passive):** confidence decay and type promotion (memory → doc) based on age and read frequency. Captures are left on the queue for a later LLM-configured run rather than discarded.

**Adaptive decay:** every `GET /memory/:id` read increments a heat counter. Before decaying an entry the archivist computes `heat = read_count × 0.9^(weeks_since_last_read)` — entries above the threshold are skipped. The archivist also records six health metrics per cycle (utilization rate, decay regret, synthesis and merge counts, net growth, contradictions) for trend analysis.

A single archivist holds a lease per deployment, so only one curates at a time. Supports Anthropic and any OpenAI-compatible provider.
