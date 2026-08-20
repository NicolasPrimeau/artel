<!-- covers: mesh, feeds -->
# Mesh and feeds

One notepad, several machines — laptop, desktop, the box under the stairs — with no cloud in the middle and no "main" copy. Write on either side, offline if you like; they reconcile when they can see each other.

Each instance publishes its notes as Atom and JSON Feed. Link two and they replicate as a CRDT — keyed by immutable id, idempotent on ingest, no central coordinator. LAN peers discover each other via mDNS (`_artel._tcp.local.`) and link with one click. Each instance's archivist only synthesizes entries it originally wrote. (Captures never cross the mesh — they are local ingest, not shared memory.)

<details>
<summary>Convergence guarantees</summary>

- **Stable identity.** Propagated entries keep their origin UUID — never re-minted on ingest.
- **No loops.** Re-receiving a known id is a no-op. Entries tagged with your own instance's origin are skipped. `A → B → A` terminates; `A → B → C` propagates.
- **Convergence.** Concurrent edits settle last-writer-wins on `version`; deletes propagate as tombstones. The topology can contain cycles safely.

Pinned by tests in `tests/test_feeds.py`.

</details>

### Subscribing to the outside world

Confusingly, "feed" means two things here. Above, it is how instances replicate to each other. It is also how the pad reads things nobody on your fleet wrote:

```bash
feed_subscribe("https://example.com/blog/atom.xml")
feed_list()
feed_unsubscribe(feed_id)
```

Point it at any RSS or Atom source — a changelog, a security advisory list, a release feed — and new items are polled and land as notes, searchable next to everything else and subject to the same decay. A dependency's breaking-change post is in the pad before an agent trips over it.
