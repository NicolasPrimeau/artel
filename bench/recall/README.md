# Recall relevance

Measures what the per-prompt recall hook injects: how much of it helps, and how often it injects something when nothing would.

`labels.json` holds 48 real prompts from the fleet's own sessions (engineering work on Artel, Nimbus and Formulai, with private names paraphrased out), each labelled with the memory ids that would genuinely change the answer. Eleven are labelled with none, because most chat turns ("it's kind of cringe though no?") have no note worth surfacing and the right move is silence. Labels were drawn from each prompt's top eight candidates, so a relevant note that retrieval never ranks is invisible here; the numbers understate misses, not noise.

`run.py` replays each prompt through the hook's own search call, in process, against a copy of a backup, with the evaluated agent treated as the archivist so no confidence is reinforced and no regret is logged. Production is never touched.

```bash
uv run python bench/recall/run.py --db ~/backups/artel/artel-<stamp>.db
```

Measured 2026-10-09 against the 2026-10-06 18:00 backup:

| Policy | Lines injected | Precision | Positives served | Noise on no-answer prompts |
| --- | --- | --- | --- | --- |
| no distance cut, two notes | 97 | 0.34 | 25/37 | 11/11 |
| `max_distance=1.18`, two notes | 80 | 0.40 | 25/37 | 7/11 |
| `max_distance=1.0`, one note | 31 | 0.65 | 20/37 | 3/11 |
| `max_distance=0.95`, one note | 20 | 0.45 | 9/37 | 0/11 |

The hook runs the third row. Silence is the typical right answer, so the policy is chosen for what it does not say: it injects 61% fewer lines than the cut it replaced and fires on three of the eleven prompts that deserve nothing instead of seven, at the cost of five positives. The fourth row shows where that trade stops paying: one step tighter and most real answers go too. `run.py` prints the whole frontier.
