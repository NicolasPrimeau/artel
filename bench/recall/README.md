# Recall relevance

Measures what the per-prompt recall hook injects: how much of it helps, and how often it injects something when nothing would.

`labels.json` holds 48 real prompts from the fleet's own sessions (engineering work on Artel, Nimbus and Formulai, with private names paraphrased out), each labelled with the memory ids that would genuinely change the answer. Eleven are labelled with none, because most chat turns ("it's kind of cringe though no?") have no note worth surfacing and the right move is silence. Labels were drawn from each prompt's top eight candidates, so a relevant note that retrieval never ranks is invisible here; the numbers understate misses, not noise.

`run.py` replays each prompt through the hook's own search call, in process, against a copy of a backup, with the evaluated agent treated as the archivist so no confidence is reinforced and no regret is logged. Production is never touched.

```bash
uv run python bench/recall/run.py --db ~/backups/artel/artel-<stamp>.db
```

Measured 2026-09-24 against the 20:00 backup:

| Policy | Precision | Positives served | Useful per prompt | Noise on no-answer prompts |
| --- | --- | --- | --- | --- |
| no distance cut | 0.38 | 25/37 | 0.77 | 11/11 |
| `max_distance=1.18` | 0.49 | 28/37 | 0.81 | 7/11 |

The cut runs before diversification, so slots it frees are refilled with closer notes, which is why it serves more positives rather than fewer. Tighter cuts and lexical-overlap rules raise precision further but start losing positives; the table printed by `run.py` shows the whole frontier.
