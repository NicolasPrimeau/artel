#!/usr/bin/env python3
"""Report which documentation pages describe code that has since changed.

Generated reference pages cannot drift — they are rebuilt from the source. Prose
pages can, silently, and that is the failure this catches. A page declares the
symbols it describes in its front matter:

    ---
    anchors:
      - artel/server/routes/tasks.py::complete_task
    ---

Each anchor's span is hashed with the same compiler compile mode uses, so a page
goes stale on exactly the same signal a compiled memory does: the code it is
anchored to moved. Editing a function body does not restale a page anchored to
the module, because the module anchor hashes the file's shape, not its bytes.

    check_docs.py            report status, exit 1 if anything is stale
    check_docs.py --json     machine-readable report
    check_docs.py --update   re-bless every anchor at its current sha

No LLM, no server, no network. Staleness is a hash comparison, not a judgement.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from artel.compile.anchors import compile_source  # noqa: E402

DOCS = ROOT / "docs"
LOCK = DOCS / ".anchors.lock"

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    meta: dict = {}
    key = None
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            meta.setdefault(key, []).append(line.lstrip()[2:].strip())
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                meta[key] = value
                key = None
    return meta


def _anchor_sha(anchor: str) -> str | None:
    """Current sha of an anchor, or None if the file or symbol no longer exists."""
    path, _, symbol = anchor.partition("::")
    source_file = ROOT / path
    if not source_file.exists():
        return None
    units = compile_source(path, source_file.read_text())
    for unit in units:
        if unit.symbol == symbol:
            return unit.sha
    return None


def collect() -> list[dict]:
    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    report = []
    for page in sorted(DOCS.rglob("*.md")):
        if "reference/" in str(page.relative_to(DOCS)):
            continue
        meta = _front_matter(page.read_text())
        for anchor in meta.get("anchors", []):
            current = _anchor_sha(anchor)
            recorded = lock.get(anchor)
            if current is None:
                status = UNKNOWN
            elif recorded is None:
                status = UNKNOWN
            elif current == recorded:
                status = FRESH
            else:
                status = STALE
            report.append(
                {
                    "page": str(page.relative_to(ROOT)),
                    "anchor": anchor,
                    "status": status,
                    "recorded": recorded,
                    "current": current,
                }
            )
    return report


def update() -> int:
    lock = {}
    for row in collect():
        if row["current"] is not None:
            lock[row["anchor"]] = row["current"]
        else:
            print(f"cannot resolve anchor, dropping: {row['anchor']}", file=sys.stderr)
    LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(f"blessed {len(lock)} anchor(s) into {LOCK.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="re-bless anchors at current shas")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args()

    if args.update:
        return update()

    report = collect()
    if args.json:
        print(json.dumps(report, indent=2))
    stale = [r for r in report if r["status"] != FRESH]
    if not args.json:
        if not report:
            print("no anchored pages yet")
            return 0
        by_page: dict[str, list[dict]] = {}
        for row in report:
            by_page.setdefault(row["page"], []).append(row)
        for page, rows in by_page.items():
            bad = [r for r in rows if r["status"] != FRESH]
            mark = "STALE" if bad else "ok"
            print(f"[{mark:>5}] {page}  ({len(rows)} anchor(s))")
            for row in bad:
                print(f"          {row['status']}: {row['anchor']}")
        print(f"\n{len(report) - len(stale)}/{len(report)} anchors fresh")
        if stale:
            print(
                "\nThe code these pages describe has changed. Re-read the page, correct it,\n"
                "then run: uv run python scripts/check_docs.py --update"
            )
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
