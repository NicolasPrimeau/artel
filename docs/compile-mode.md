<!-- covers: compile, graph -->
# Compile mode

Notes about code go stale the moment someone edits the code. A note that says "the retry lives in `client.py`" is worse than no note at all once the retry moves — it sends you confidently to the wrong place.

So pin those notes to the code itself. A **compiled** note is anchored to a symbol; when that symbol changes, the note re-derives instead of quietly rotting. It doesn't decay with age, because age was never what made it wrong.

Most notes are **authored** — judgement, incidents, intent — and those have no ground truth to check against, so they decay if they go unread. Compiled notes do have one, so they get held to it. (Mesh is the mirror image: many machines converging on one set of notes, where this is one set of notes converging on the code it describes.)

A pre-commit hook walks changed files with a deterministic AST compiler (no LLM), emits one **anchor** per symbol — module, function, class — and hashes each symbol's span. Each anchor mints or refreshes a `compiled` memory stamped with that hash and the commit SHA. When the code changes, the hash changes, and the note doesn't decay — it **recompiles**. Memory that's wrong about the code is rebuilt, not slowly forgotten.

**Authored and compiled are endpoints of a continuum, not two modes.** They share one store, one search index, one API. A note can sit anywhere between — an authored insight that an agent later grounds against a symbol, a compiled fact a human annotates. The same `GET /memory/search` returns both.

**The knowledge graph** is what makes the continuum real. Memories and code anchors are nodes of one heterogeneous graph; edges are typed:

- `grounds` — an anchor grounds a memory in real code
- `relies_on` — one node's meaning depends on another's (the dependency graph of meaning)
- `applies_to` — an authored note applies to a region of code
- `corroborates` / `contradicts` — agreement and tension between notes

Invalidation propagates **backward along `relies_on`**, exactly like `gcc -MMD` incremental builds: change `g`, and every compiled note that relies on `g` is marked stale, transitively. The module anchor hashes the file's *shape* (its sorted imports and top-level symbols), not its bytes, so editing one function body doesn't restale the whole module.

**Viability is connectivity — derived, never stored.** There's no "groundedness" score. An ungrounded memory is just a bare node on the graph, and a bare node is forgettable. The more a memory is connected — fresh groundings, corroborations, things that rely on it — the more viable it is; contradictions and stale groundings pull it down:

```
raw   = fresh_grounds + 0.5·backlinks + 0.3·corroborates − contradictions − 0.5·stale_grounds
score = 0           if raw ≤ 0
        1 − 2^(−raw) otherwise
```

So a fresh, grounded note that nothing disputes scores well; the moment something contradicts it the score collapses toward zero. The computation is live — `GET /graph/:id` recomputes it from the current edges every time, so nothing can go stale behind your back.

**Why you can trust it.** A compiled note carries the source SHA it was built from. Freshness is a hash comparison, not a judgement call: `POST /compile/check` answers *fresh / stale / unknown* per symbol. Fresh means the code hasn't moved since the note was built — you can act on the note without re-reading the code. That's the whole point: trustworthy enough to *not* check.

**Setup is one line — or just ask.** Tell any connected agent *"set up compile mode"* and it calls the `compile_setup` MCP tool, which hands back the installer. Or run it yourself from the repo root:

```bash
# installs a pre-commit hook: a single self-contained, stdlib-only Python file.
# no `pip install` in your repo, and it's a safe no-op until creds are set.
curl -fsSL "$ARTEL/compile/install.sh" | sh
export ARTEL_AGENT_ID=myagent ARTEL_AGENT_KEY=… ARTEL_PROJECT=myrepo

# seed the whole repo once; later commits compile only what changed
python3 "$(git rev-parse --show-toplevel)/.git/hooks/artel_compile.py" --all

# inspect compile health and the graph
curl "$ARTEL/compile/stale?project=myrepo"        # notes whose code moved out from under them
curl "$ARTEL/graph/$NODE_ID"                       # node, edges, live viability
```

Every property above — SHA freshness, `relies_on` invalidation, module-shape stability across body edits, viability collapsing on contradiction, compiled memory never decaying or merging — is pinned by `tests/test_compile.py`. The compiler is deterministic and LLM-free, so the tests are exact, not probabilistic.
