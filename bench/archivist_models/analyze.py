import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import httpx

from artel.archivist.compaction import _strip_fences
from artel.archivist.synthesis import _parse_operations

WORK = Path.home() / ".cache" / "artel-bakeoff"
BASELINE = "google/gemini-3.7-flash"
DAY_IN = 730_000
DAY_OUT = 150_000


def parse(pass_name, text):
    if pass_name == "run_headlines":
        line = text.strip().splitlines()[0].strip() if text.strip() else ""
        return bool(line), line
    try:
        data = json.loads(_strip_fences(text))
    except Exception:
        return False, None
    if pass_name == "_llm_ops_pass":
        return isinstance(data, list), _parse_operations(text) if isinstance(data, list) else None
    if pass_name == "_extract_with_llm":
        return isinstance(data, dict), data if isinstance(data, dict) else None
    if pass_name == "_refine_with_llm":
        ok = isinstance(data, dict) and isinstance(data.get("ops"), list)
        return ok, data.get("ops") if ok else None
    return isinstance(data, dict | list), data


def signature(pass_name, parsed):
    if parsed is None:
        return None
    if pass_name == "_llm_ops_pass":
        return Counter(op.get("op") for op in parsed)
    if pass_name == "_extract_with_llm":
        return Counter({k: len(parsed.get(k) or []) for k in ("facts", "updates", "decisions")})
    if pass_name == "_refine_with_llm":
        return Counter(op.get("action") for op in parsed)
    return None


def agreement(a, b):
    if a is None or b is None:
        return None
    keys = set(a) | set(b)
    total = sum(max(a[k], b[k]) for k in keys)
    if not total:
        return 1.0
    return sum(min(a[k], b[k]) for k in keys) / total


def main(prompts_path, results_path, examples_path):
    prompts = {p["n"]: p for p in json.loads(prompts_path.read_text())}
    results = json.loads(results_path.read_text())
    rates = {
        m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"]))
        for m in httpx.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]
    }
    base_rows = {r["n"]: r for r in results.get(BASELINE, [])}
    base_in = sum(r.get("prompt_tokens", 0) for r in base_rows.values())
    base_out = sum(r.get("completion_tokens", 0) for r in base_rows.values())
    base_parsed = {
        n: parse(prompts[n]["pass"], r.get("content", ""))[1]
        for n, r in base_rows.items()
        if "error" not in r
    }
    examples = []
    print(
        "model | parsed | truncated | errors | $ sample | $/day projected | p50 s | p95 s | agree"
    )
    for model, rows in results.items():
        ok = trunc = err = 0
        agrees = []
        for r in rows:
            p = prompts[r["n"]]
            if "error" in r:
                err += 1
                continue
            good, parsed = parse(p["pass"], r["content"])
            ok += good
            trunc += r["finish"] == "length"
            if model != BASELINE:
                a = agreement(
                    signature(p["pass"], base_parsed.get(r["n"])), signature(p["pass"], parsed)
                )
                if a is not None:
                    agrees.append(a)
                    if a < 0.5:
                        examples.append(
                            {
                                "model": model,
                                "n": r["n"],
                                "pass": p["pass"],
                                "agreement": a,
                                "baseline": base_rows[r["n"]]["content"],
                                "candidate": r["content"],
                            }
                        )
        live = [r for r in rows if "error" not in r]
        lat = sorted(r["latency"] for r in live) or [0]
        tin = sum(r.get("prompt_tokens", 0) for r in live)
        tout = sum(r.get("completion_tokens", 0) for r in live)
        rin, rout = rates.get(model.split("@")[0], (0, 0))
        per_day = (
            DAY_IN * (tin / base_in if base_in else 1) * rin
            + DAY_OUT * (tout / base_out if base_out else 1) * rout
        )
        cost = sum(r.get("cost") or 0 for r in live)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        agree = f"{statistics.mean(agrees):.2f}" if agrees else "-"
        print(
            f"{model} | {ok}/{len(rows)} | {trunc} | {err} | ${cost:.4f} | ${per_day:.3f}"
            f" | {statistics.median(lat):.1f} | {p95:.1f} | {agree}"
        )
    examples_path.write_text(json.dumps(examples, indent=1))
    print(f"{len(examples)} low-agreement examples -> {examples_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default=str(WORK / "prompts-all.json"))
    ap.add_argument("--results", default=str(WORK / "results.json"))
    ap.add_argument("--examples", default=str(WORK / "disagreements.json"))
    a = ap.parse_args()
    main(Path(a.prompts), Path(a.results), Path(a.examples))
