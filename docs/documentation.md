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

Module anchors hash the file's *shape* — its imports and top-level symbols — not its bytes. Editing a function body does not restale a page anchored to the module, only one anchored to that symbol. A third form, `path::*`, hashes the whole file, for files whose content *is* data: adding a table to a SQL schema string moves no symbol, so a module anchor is blind to it.

**Choose the anchor that matches what the page actually claims.** A protocol spec describing the API should anchor to the models and routes, not to the storage schema — otherwise every internal table addition raises a false alarm, and an anchor that cries wolf gets ignored. Equally, a module anchor on a file that is pure data will never fire at all, which is worse than no anchor because it looks like coverage.

!!! warning "Blessing an already-stale page freezes the error"
    Anchors only detect drift *after* the point you blessed them. If a page was already wrong when you added its anchor, the lockfile records that as correct and nothing will ever flag it. Read the page against the code the first time you anchor it — this exact trap was hit while setting this up, and `spec.md` had been documenting entry types and scopes that no longer existed.

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

## Testing for silence

The failure mode this codebase actually produces is not a crash — it is a quiet,
believable wrong answer. Captures digested but never acknowledged. An inert plugin
reporting "0 tokens of overhead". An unauthenticated CLI returning empty patches
indistinguishable from failed fixes. A docs anchor blessing a page that was
already wrong.

Ordinary tests miss all of these, because a mock always behaves and the assertion
is usually "it worked" rather than "a break would be noticed".

`scripts/sabotage.py` reintroduces each real failure and asks whether anything
goes red. It found a live one immediately: making an unknown done-check *pass*
instead of fail left the entire suite green, so a typo'd check kind would have
satisfied every gate in a blueprint silently.

```bash
uv run python scripts/sabotage.py    # 7/7 caught, on a clean tree
```

Two rules that fall out of it, and that most of this week's bugs violated:

- **Never let one value mean both "working, nothing to do" and "broken".** Zero
  injected tokens meant both. Separate the liveness signal from the volume signal.
- **A gate that cannot evaluate itself must refuse, never wave through.** Every
  degrade-to-allow path here has eventually fired.

## What is deliberately not automated

An agent does not write these docs unattended. Drift *detection* is safe to automate because it is deterministic. Drift *correction* is not: a model that fluently misdescribes a parameter produces something worse than a stale page, because a reader cannot tell it is wrong. Stale docs teach distrust; confidently wrong docs waste an hour.

So the loop stops at filing work: the checker flags a page, `--open-task` puts it on the board, and a human or agent reads the diff and rewrites the prose. The rewrite goes through review like any other change.

If the rewrite is ever automated, it should produce a pull request — never a direct publish. The checker deliberately has no path that edits a page.
