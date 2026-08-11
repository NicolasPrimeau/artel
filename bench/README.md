# Artel on/off benchmark

Measures what the Artel plugin costs Claude Code on SWE-bench Verified. Same
model, same instances, same order — the only variable is whether the plugin is
active.

## What this can and cannot answer

**It cannot show Artel makes Claude Code better at isolated bugs.** A SWE-bench
instance is a fresh checkout with one issue and no history. Artel's value is
cross-session memory and coordination; on a single-shot task there is nothing to
remember from and nobody to coordinate with. Expect flat.

**It can show what Artel costs**, which is the prerequisite claim: if the plugin
degrades single-shot performance, the continuity benefits do not matter to anyone
evaluating it. So the analysis reports a *non-inferiority* result — an upper
bound on harm — not a superiority test.

**One place a benefit could appear.** The 500 Verified instances come from about
a dozen repos — django alone is 231 of them. Walking a repo in order, instance N
is the only setting where memory accumulated from instances 1..N-1 could help.
`analyze.py` reports advantage as a function of position within repo. A positive
shift is the signature of accumulated memory; a flat profile means overhead.

**Do not compare against published numbers.** Anthropic's SWE-bench figures come
from their own scaffold, prompt and retry policy. A delta against them measures
scaffold differences with Artel as a rounding error. Both arms must be run here.

## The injection gate

An Artel plugin with bad credentials returns nothing, logs nothing, injects
nothing — and still costs its latency. A treatment arm in that state is a placebo
that produces a clean, meaningless null.

`run.py` probes the hooks before the run and before every treatment instance, and
aborts after three consecutive inert instances. Verify independently first:

```bash
uv run python scripts/measure_hook_overhead.py   # exits non-zero if inert
```

## Running it

```bash
# 1. select instances — largest repos, ordered by creation date within each
uv run python bench/select.py --repos 3 --out bench/instances.json

# 2. smoke test: two instances, both arms, confirm patches come out
uv run python bench/run.py --limit 2 --model sonnet --out bench/runs/smoke

# 3. the pilot
uv run python bench/run.py --instances bench/instances.json \
    --model sonnet --out bench/runs/pilot

# 4. score both arms with the official harness (needs docker; slow)
python -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --predictions_path bench/runs/pilot/preds-control-<runid>.jsonl \
    --run_id pilot-control --max_workers 8
#   ... and again for preds-treatment-<runid>.jsonl

# 5. analyse
uv run python bench/analyze.py --metrics 'bench/runs/pilot/metrics-*.jsonl' \
    --report-control <control report>.json \
    --report-treatment <treatment report>.json
```

### Memory modes

`--memory isolated` (default) gives the treatment arm a fresh project, so the
only memory that exists is what the benchmark itself accumulates. This tests the
continuity hypothesis.

`--memory fleet` points it at the real store, where nothing is relevant to a
django bug. That is the adversarial overhead condition: real, plausible,
useless context. Zero possible benefit, so anything it costs is pure cost.

## Sizing it

`analyze.py` prints the sample size needed for your margin. As a guide: at 5%
discordance between arms, roughly **215 paired instances** give a ±3 pp interval.
Fewer than that and a null result means "underpowered", not "no harm" — the
script says so rather than letting the ambiguity pass.

Start with Sonnet. Only escalate to Opus if the pilot shows something, since the
run cost doubles per arm and the expensive question is capability, not overhead.

## Honest caveats

- Overhead is continuous and low-variance, so it is trustworthy at small n.
  Capability is a rate, and needs hundreds of instances to say anything.
- Every instance runs with `--dangerously-skip-permissions` in a scratch clone.
- `run.py` does not score patches. Scoring is the official harness's job; a
  home-grown scorer would be the easiest place to accidentally fake a result.
- If a gain appears, check it is not coming from near-duplicate issues within a
  repo. That is memorising an answer, not transferable knowledge.
