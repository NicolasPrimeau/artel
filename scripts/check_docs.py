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

Anchors catch prose that ROTTED. They cannot catch prose that was never written:
a primitive shipped with no page has no anchor, so there is nothing to compare and
the gate stays green forever. Blueprints and decisions each shipped a full set of
MCP tools and REST routes that way and went unmentioned for releases.

So coverage is checked too. Every MCP tool group and every route module in the code
must be claimed by a prose page with a marker placed next to the prose that covers it:

    <!-- covers: blueprints -->

Markers are read from README.md and docs/*.md. docs/reference/ is excluded — it is
generated from the source, so it always "mentions" everything and would make the
check vacuous. A marker naming a surface that no longer exists fails too, so claims
cannot outlive the code.

    check_docs.py             report status, exit 1 if anything is stale or uncovered
    check_docs.py --json      machine-readable report
    check_docs.py --update    re-bless every anchor at its current sha
    check_docs.py --open-task file an Artel task for the stale pages

The check itself needs no LLM, no server and no network. Staleness is a hash
comparison, not a judgement. --open-task is the only part that talks to Artel,
and it only ever files work for a human or agent to do — it never edits a page.
"""

import argparse
import ast
import json
import re
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


CONTENT_ANCHOR = "*"


def _anchor_sha(anchor: str) -> str | None:
    """Current sha of an anchor, or None if the file or symbol no longer exists."""
    path, _, symbol = anchor.partition("::")
    source_file = ROOT / path
    if not source_file.exists():
        return None
    if symbol == CONTENT_ANCHOR:
        # Whole-file hash. A module anchor hashes the file's SHAPE — imports and
        # top-level symbol names — so it is blind to a file whose content is data:
        # adding a table to a SQL schema string moves no symbol. Use this form for
        # those, and the module form when you mean the interface.
        import hashlib

        return hashlib.sha256(source_file.read_bytes()).hexdigest()
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


TASK_TAG = "docs-freshness"


def _artel() -> tuple[str, dict] | None:
    import os

    url = os.environ.get("ARTEL_URL")
    agent = os.environ.get("ARTEL_AGENT_ID")
    key = os.environ.get("ARTEL_API_KEY") or os.environ.get("ARTEL_KEY")
    if not (url and agent and key):
        print(
            "set ARTEL_URL, ARTEL_AGENT_ID and ARTEL_API_KEY to file a task",
            file=sys.stderr,
        )
        return None
    return url.rstrip("/"), {"x-agent-id": agent, "x-api-key": key}


def open_task(stale: list[dict]) -> int:
    """File one task for the current stale set, or comment on the open one.

    Deliberately idempotent: a check that runs on every commit must not breed a
    task per commit for the same drift.
    """
    import httpx

    creds = _artel()
    if creds is None:
        return 1
    url, headers = creds
    pages = sorted({r["page"] for r in stale})
    body = "\n".join(f"- {r['page']} -> {r['anchor']} ({r['status']})" for r in stale)
    with httpx.Client(base_url=url, headers=headers, timeout=30) as c:
        try:
            existing = c.get("/tasks", params={"status": "open", "tag": TASK_TAG})
            existing.raise_for_status()
            open_tasks = existing.json()
        except Exception as e:
            print(f"could not reach Artel: {e}", file=sys.stderr)
            return 1
        if open_tasks:
            task_id = open_tasks[0]["id"]
            c.post(f"/tasks/{task_id}/comments", json={"body": f"Still stale:\n{body}"})
            print(f"commented on open task {task_id[:8]}")
            return 0
        r = c.post(
            "/tasks",
            json={
                "title": f"Documentation is stale: {len(pages)} page(s) describe changed code",
                "description": (
                    "scripts/check_docs.py found prose pages whose anchored code has moved.\n\n"
                    f"{body}\n\n"
                    "Re-read each page against the current code, correct what is now wrong, "
                    "then run `uv run python scripts/check_docs.py --update` to re-bless the "
                    "anchors. Do not re-bless without reading — that just hides the drift."
                ),
                "expected_outcome": "Each listed page reads true against current code and check_docs.py exits 0.",
                "priority": "normal",
                "tags": [TASK_TAG, "docs"],
            },
        )
        if r.status_code >= 400:
            print(f"could not create task: {r.status_code} {r.text[:200]}", file=sys.stderr)
            return 1
        print(f"filed task {r.json()['id'][:8]} for {len(pages)} stale page(s)")
    return 0


MARKER = re.compile(r"<!--\s*covers:\s*([^>]+?)\s*-->", re.I)


def _surfaces() -> dict[str, str]:
    """Every surface the code exposes, as {name: what it is}.

    Read from the source rather than a hand-kept list, because a list is exactly
    the thing that stops being updated when someone ships a new primitive.

    Route modules name the surface; MCP tool groups fold into the module that
    serves them (task_* into tasks.py) so one claim covers a primitive rather than
    demanding a separate one per transport.
    """
    routes: dict[str, str] = {}
    for route in sorted((ROOT / "artel" / "server" / "routes").glob("*.py")):
        if route.stem.startswith("_"):
            continue
        if re.search(r"@router\.(get|post|put|patch|delete)", route.read_text()):
            routes[route.stem] = "REST routes"

    found = dict(routes)
    tree = ast.parse((ROOT / "artel" / "mcp" / "server.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any("tool" in ast.dump(d) for d in node.decorator_list):
            continue
        prefix = node.name.split("_")[0]
        for candidate in (prefix, prefix + "s", prefix + "es"):
            if candidate in routes:
                found[candidate] = "REST routes + MCP tools"
                break
        else:
            found.setdefault(prefix, "MCP tools")
    return found


def _claims() -> dict[str, list[str]]:
    """What each prose page says it covers. docs/reference/ is deliberately excluded."""
    claimed: dict[str, list[str]] = {}
    pages = [ROOT / "README.md"] + sorted(DOCS.glob("*.md"))
    for page in pages:
        if not page.exists():
            continue
        for match in MARKER.finditer(page.read_text()):
            for name in match.group(1).split(","):
                name = name.strip().lower()
                if name:
                    claimed.setdefault(name, []).append(str(page.relative_to(ROOT)))
    return claimed


def coverage() -> tuple[list[str], list[str]]:
    """(surfaces with no prose, claims naming a surface that does not exist)."""
    surfaces = _surfaces()
    claimed = _claims()
    # Route modules and tool groups often describe the same primitive (tasks.py and
    # task_*), so a single claim satisfies both; they are keyed by the same name.
    uncovered = [
        f"{name} ({what}) — no page claims it"
        for name, what in sorted(surfaces.items())
        if name not in claimed
    ]
    orphaned = [
        f"{name} — claimed by {', '.join(pages)} but no such surface in the code"
        for name, pages in sorted(claimed.items())
        if name not in surfaces
    ]
    return uncovered, orphaned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="re-bless anchors at current shas")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument(
        "--open-task", action="store_true", help="file an Artel task for stale pages"
    )
    args = parser.parse_args()

    if args.update:
        return update()

    report = collect()
    uncovered, orphaned = coverage()
    if args.json:
        print(
            json.dumps({"anchors": report, "uncovered": uncovered, "orphaned": orphaned}, indent=2)
        )
    stale = [r for r in report if r["status"] != FRESH]
    if args.open_task:
        if not stale:
            print("nothing stale; no task filed")
            return 0
        return open_task(stale)
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
        surfaces = len(_surfaces())
        print(f"{surfaces - len(uncovered)}/{surfaces} surfaces covered")
        if uncovered:
            print("\nShipped but undocumented — the code exposes these and no page claims them:")
            for row in uncovered:
                print(f"  {row}")
            print(
                "\nWrite the prose, then mark the section that covers it:\n"
                "  <!-- covers: <surface> -->"
            )
        if orphaned:
            print("\nClaims that outlived their code:")
            for row in orphaned:
                print(f"  {row}")
        if stale:
            print(
                "\nThe code these pages describe has changed. Re-read the page, correct it,\n"
                "then run: uv run python scripts/check_docs.py --update"
            )
    return 1 if (stale or uncovered or orphaned) else 0


if __name__ == "__main__":
    raise SystemExit(main())
