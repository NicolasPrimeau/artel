# How these docs stay true

Documentation rots because it hand-duplicates things that already have a source of truth. Artel's docs are built so the parts that can be derived *are* derived, and the parts that can't are watched.

## Two kinds of page

**Reference pages are generated.** [MCP tools](reference/mcp-tools.md), [REST API](reference/rest.md), and [Configuration](reference/configuration.md) are produced by `scripts/gen_docs.py` from the tool docstrings, `openapi.json`, and the `BaseSettings` classes. They are regenerated at publish time rather than committed, so there is no copy that can disagree with the code. Never edit them by hand — your edit will vanish on the next build.

```bash
uv run python scripts/gen_docs.py    # writes docs/reference/*.md
```

Config *descriptions* are the one thing that cannot be derived — pydantic fields carry no help text — so they come from `SETTING_NOTES` in the generator. The generator emits every field it finds regardless, so a new setting can never be silently missing: it appears marked **Undocumented** and the run warns.

**Prose pages are watched.** Concept and guide pages are written by hand, and nothing can generate them. Instead they declare the code they describe, in front matter:

```yaml
---
anchors:
  - artel/server/auth.py
  - artel/server/auth.py::role_of
---
```

An anchor is `path` for a whole module, or `path::symbol` for one function or class. `scripts/check_docs.py` hashes each anchor's span using the same compiler [compile mode](https://github.com/NicolasPrimeau/artel#compile-mode) uses, and compares it to `docs/.anchors.lock`.

```bash
uv run python scripts/check_docs.py           # report; exit 1 if any page is stale
uv run python scripts/check_docs.py --json    # machine-readable
uv run python scripts/check_docs.py --update  # re-bless after correcting a page
```

A page goes stale on exactly the signal a compiled memory does: the code it is anchored to moved. This is a hash comparison, not a judgement — there is no model involved and no way for it to be confidently wrong.

Module anchors hash the file's *shape* — its imports and top-level symbols — not its bytes. Editing a function body does not restale a page anchored to the module, only one anchored to that symbol. Choose the granularity that matches what the page actually claims.

## Where it runs

| Where | What happens |
| --- | --- |
| `pre-commit` | Reports stale pages. Never blocks a commit. |
| CI, every push to master | Regenerates reference, warns on stale prose, builds, publishes to GitHub Pages. |
| `--open-task` | Files the drift as an Artel task for someone to act on. |

```bash
export ARTEL_URL=... ARTEL_AGENT_ID=... ARTEL_API_KEY=...
uv run python scripts/check_docs.py --open-task
```

It is idempotent: if a `docs-freshness` task is already open it comments on that one instead of filing another, so running it on every commit cannot breed a task per commit.

The freshness check is deliberately non-blocking. A prose page lagging the code by one commit should be visible, not a merge gate.

## Adding a page

1. Write it in `docs/`, add it to `nav` in `mkdocs.yml`.
2. If it describes specific code, add `anchors` front matter and run `check_docs.py --update`.
3. `uv run mkdocs serve` to preview locally.

## What is deliberately not automated

An agent does not write these docs unattended. Drift *detection* is safe to automate because it is deterministic. Drift *correction* is not: a model that fluently misdescribes a parameter produces something worse than a stale page, because a reader cannot tell it is wrong. Stale docs teach distrust; confidently wrong docs waste an hour.

So the loop stops at filing work: the checker flags a page, `--open-task` puts it on the board, and a human or agent reads the diff and rewrites the prose. The rewrite goes through review like any other change.

If the rewrite is ever automated, it should produce a pull request — never a direct publish. The checker deliberately has no path that edits a page.
