#!/usr/bin/env python3
"""Post historical token usage from local transcripts into the ledger.

The drainer only sees sessions from the moment it ships; everything before that is on
disk and invisible. This reads the same transcripts with the same extractor the hook
uses, so a backfill and a live drain cannot disagree about what a session cost.

Re-runnable: the server dedups on (session_id, model, window_end).
"""

import argparse
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _artel_hooks import usage_rollup  # noqa: E402

ROOT = pathlib.Path.home() / ".claude" / "projects"
PREFIXES = ("-home-nprimeau-projects-", "-home-nprimeau-")


def project_for(dirname: str, mapping: dict[str, str]) -> str | None:
    name = dirname
    for p in PREFIXES:
        if name.startswith(p):
            name = name[len(p) :]
            break
    return mapping.get(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--billing-mode", default="subscription")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import datetime

    cut = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=args.days)).timestamp()
    mapping = {
        "Nimbus": "nimbus",
        "formulai": "formulai",
        "Artel": "artel",
        "Yeet": "yeet",
        "Polaris": "polaris",
        "Longshot": "longshot",
        "nprimeau.dev": "blog",
        "Steward": "lighthouse",
    }
    sent = skipped = 0
    for d in sorted(ROOT.iterdir()) if ROOT.is_dir() else []:
        if not d.is_dir():
            continue
        project = project_for(d.name, mapping)
        for f in sorted(d.glob("*.jsonl")):
            try:
                if f.stat().st_mtime < cut:
                    continue
            except OSError:
                continue
            rollups = usage_rollup(f.read_text(errors="replace").splitlines())
            for model, row in rollups.items():
                body = dict(row, model=model, session_id=f.stem, billing_mode=args.billing_mode)
                if project:
                    body["project"] = project
                if not args.apply:
                    skipped += 1
                    continue
                req = urllib.request.Request(
                    args.url.rstrip("/") + "/usage",
                    method="POST",
                    data=json.dumps(body).encode(),
                    headers={
                        "content-type": "application/json",
                        "x-agent-id": args.agent,
                        "x-api-key": args.key,
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=20):
                        sent += 1
                except Exception as e:
                    print(f"  failed {f.stem[:8]}/{model}: {e}")
    print(f"  {'posted' if args.apply else 'would post'}: {sent or skipped} rollups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
