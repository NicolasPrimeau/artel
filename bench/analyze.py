#!/usr/bin/env python3
"""Analyse a paired Artel on/off run.

The question is NOT "does Artel make Claude Code better at isolated bugs" — it
almost certainly cannot, since a SWE-bench instance has no history to remember.
The question is "what does it cost", so this reports a NON-INFERIORITY result:
an upper bound on the harm, plus the measured overhead in time and tokens.

Superiority and non-inferiority are not the same test. "No significant
difference" is not evidence of no harm — with a small sample it is mostly
evidence of a small sample. What follows reports the confidence interval, so a
wide one is visibly uninformative rather than quietly reassuring.

    uv run python bench/analyze.py --metrics bench/runs/pilot/metrics-*.jsonl \\
        --report-control <swebench report>.json --report-treatment <...>.json
"""

import argparse
import glob
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

# Pre-specify the margin. Deciding it after seeing the data is how you talk
# yourself into a result.
DEFAULT_MARGIN_PP = 3.0


def load_metrics(patterns: list[str]) -> list[dict]:
    rows = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            for line in Path(path).read_text().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def load_resolved(report_path: str | None) -> set[str]:
    if not report_path:
        return set()
    data = json.loads(Path(report_path).read_text())
    for key in ("resolved_ids", "resolved"):
        if key in data:
            return set(data[key])
    return {k for k, v in data.items() if isinstance(v, dict) and v.get("resolved")}


def mcnemar(both: int, only_control: int, only_treatment: int) -> tuple[float, str]:
    """Exact-ish McNemar on the discordant pairs — the only pairs carrying signal."""
    n = only_control + only_treatment
    if n == 0:
        return 1.0, "no discordant pairs"
    # normal approximation with continuity correction; fine above ~10 discordant
    chi2 = (abs(only_control - only_treatment) - 1) ** 2 / n
    p = math.erfc(math.sqrt(chi2 / 2))
    note = "" if n >= 10 else f"only {n} discordant pairs — underpowered"
    return p, note


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def paired_diff_ci(only_control: int, only_treatment: int, n_pairs: int) -> tuple[float, float]:
    """95% CI on (treatment - control) resolve rate, from discordant counts."""
    if n_pairs == 0:
        return (0.0, 0.0)
    diff = (only_treatment - only_control) / n_pairs
    var = (only_treatment + only_control) / (n_pairs**2)
    half = 1.96 * math.sqrt(var) if var > 0 else 0.0
    return (diff - half, diff + half)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", nargs="+", required=True)
    parser.add_argument("--report-control")
    parser.add_argument("--report-treatment")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN_PP)
    args = parser.parse_args()

    rows = load_metrics(args.metrics)
    by_arm: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        by_arm[r["arm"]][r["instance_id"]] = r

    control, treatment = by_arm.get("control", {}), by_arm.get("treatment", {})
    paired = sorted(set(control) & set(treatment))
    print(f"instances: control={len(control)} treatment={len(treatment)} paired={len(paired)}\n")

    # ---- overhead: continuous, low variance, informative at small n -------------
    print("OVERHEAD (the question this benchmark can actually answer at this size)")
    for label, key in (
        ("wall seconds", "wall_seconds"),
        ("turns", "num_turns"),
        ("cost usd", "cost_usd"),
    ):
        c = [control[i].get(key) for i in paired if isinstance(control[i].get(key), (int, float))]
        t = [
            treatment[i].get(key) for i in paired if isinstance(treatment[i].get(key), (int, float))
        ]
        if c and t:
            cm, tm = statistics.median(c), statistics.median(t)
            delta = f"{tm - cm:+.1f}" if cm else "n/a"
            pct = f"{(tm / cm - 1) * 100:+.0f}%" if cm else ""
            print(f"  {label:14} control {cm:9.2f}   treatment {tm:9.2f}   {delta:>8} {pct}")
    injected = [
        treatment[i].get("injected_tokens")
        for i in paired
        if isinstance(treatment[i].get("injected_tokens"), int)
    ]
    if injected:
        print(f"  {'injected tok':14} median {statistics.median(injected):.0f} per probe")
        if not any(injected):
            print("  WARNING: zero injection throughout — the treatment arm was inert.")

    # ---- capability: needs the official evaluation ------------------------------
    resolved_c = load_resolved(args.report_control)
    resolved_t = load_resolved(args.report_treatment)
    if not (resolved_c or resolved_t):
        print("\nNo evaluation reports supplied — run the SWE-bench harness for resolve rates.")
        return 0

    both = sum(1 for i in paired if i in resolved_c and i in resolved_t)
    only_c = sum(1 for i in paired if i in resolved_c and i not in resolved_t)
    only_t = sum(1 for i in paired if i not in resolved_c and i in resolved_t)
    neither = len(paired) - both - only_c - only_t

    rate_c, rate_t = (both + only_c) / len(paired), (both + only_t) / len(paired)
    print("\nCAPABILITY (paired)")
    print(
        f"  control   resolved {both + only_c:4}/{len(paired)}  {rate_c:6.1%}  95% CI {wilson(both + only_c, len(paired))[0]:.1%}-{wilson(both + only_c, len(paired))[1]:.1%}"
    )
    print(
        f"  treatment resolved {both + only_t:4}/{len(paired)}  {rate_t:6.1%}  95% CI {wilson(both + only_t, len(paired))[0]:.1%}-{wilson(both + only_t, len(paired))[1]:.1%}"
    )
    print(f"  both {both}   control-only {only_c}   treatment-only {only_t}   neither {neither}")

    p, note = mcnemar(both, only_c, only_t)
    lo, hi = paired_diff_ci(only_c, only_t, len(paired))
    print(f"\n  McNemar p = {p:.3f} {('(' + note + ')') if note else ''}")
    print(
        f"  difference (treatment - control): {rate_t - rate_c:+.1%}  95% CI [{lo:+.1%}, {hi:+.1%}]"
    )

    margin = -args.margin / 100
    # What would it take? Half-width = 1.96*sqrt(discordance/n); solve for n.
    discordance = (only_c + only_t) / len(paired) if paired else 0.0
    if discordance > 0:
        needed = math.ceil(1.96**2 * discordance / (args.margin / 100) ** 2)
        print(
            f"  discordance {discordance:.1%} -> ~{needed} paired instances give a"
            f" +/-{args.margin:.0f} pp interval ({len(paired)} run)"
        )

    print(f"\n  NON-INFERIORITY at a {args.margin:.0f} pp margin:")
    if lo > margin:
        print(
            f"    PASS — harm worse than {args.margin:.0f} pp is excluded (CI lower bound {lo:+.1%})."
        )
    else:
        print(f"    NOT SHOWN — CI lower bound {lo:+.1%} allows harm beyond the margin.")
        print("    This is an inconclusive result, not a negative one: more instances needed.")

    # ---- continuity: the only place a benefit could appear ----------------------
    print("\nCONTINUITY (advantage vs position within repo — where accumulated memory could help)")
    buckets: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i in paired:
        pos = treatment[i].get("position_in_repo")
        if isinstance(pos, int):
            buckets[treatment[i]["repo"]].append(
                (pos, (1 if i in resolved_t else 0) - (1 if i in resolved_c else 0))
            )
    for repo, pairs in sorted(buckets.items()):
        pairs.sort()
        half = len(pairs) // 2
        if half < 2:
            continue
        early = statistics.mean(d for _, d in pairs[:half])
        late = statistics.mean(d for _, d in pairs[half:])
        print(f"  {repo:26} early {early:+.2f}   late {late:+.2f}   shift {late - early:+.2f}")
    print("\n  A positive shift is the only signature of accumulated memory helping.")
    print("  A flat profile means the plugin is overhead on this workload — which is")
    print("  the expected result, and a legitimate finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
