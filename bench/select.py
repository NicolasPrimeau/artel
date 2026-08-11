#!/usr/bin/env python3
"""Pick the pilot instance set from SWE-bench Verified.

Selection is deterministic and repo-grouped. Repo grouping is not cosmetic: the
500 Verified instances come from about a dozen repos, and that repetition is the
only continuity axis the benchmark has. Instance N of a repo is the only place
accumulated memory could possibly help, so the runner walks a repo in order and
the analysis looks at advantage as a function of position within it.

    uv run python bench/select.py --repos 3 --out bench/instances.json
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

DATASET = "princeton-nlp/SWE-bench_Verified"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
PAGE = 100
KEEP_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "environment_setup_commit",
    "problem_statement",
    "version",
    "difficulty",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)


def fetch_all() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode(
            {
                "dataset": DATASET,
                "config": "default",
                "split": "test",
                "offset": offset,
                "length": PAGE,
            }
        )
        with urllib.request.urlopen(f"{ROWS_URL}?{params}", timeout=60) as fh:
            payload = json.load(fh)
        batch = [r["row"] for r in payload["rows"]]
        rows.extend(batch)
        total = payload["num_rows_total"]
        offset += len(batch)
        print(f"  fetched {len(rows)}/{total}", file=sys.stderr)
        if offset >= total or not batch:
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos", type=int, default=3, help="how many of the largest repos")
    parser.add_argument("--per-repo", type=int, default=0, help="cap instances per repo (0 = all)")
    parser.add_argument("--out", default="bench/instances.json")
    args = parser.parse_args()

    rows = fetch_all()
    counts = Counter(r["repo"] for r in rows)
    chosen_repos = [repo for repo, _ in counts.most_common(args.repos)]

    # Ordered by creation date within a repo: the arrival order a real fleet would
    # have seen, so "position in repo" means something.
    selected: list[dict] = []
    for repo in chosen_repos:
        instances = sorted(
            (r for r in rows if r["repo"] == repo),
            key=lambda r: (r["created_at"], r["instance_id"]),
        )
        if args.per_repo:
            instances = instances[: args.per_repo]
        for position, row in enumerate(instances):
            entry = {k: row[k] for k in KEEP_FIELDS if k in row}
            entry["position_in_repo"] = position
            selected.append(entry)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(selected, indent=2) + "\n")

    print(f"\n{len(rows)} instances across {len(counts)} repos")
    for repo, n in counts.most_common(args.repos):
        picked = sum(1 for s in selected if s["repo"] == repo)
        print(f"  {repo:34} {n:4} available  {picked:4} selected")
    print(f"\nwrote {len(selected)} instances to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
