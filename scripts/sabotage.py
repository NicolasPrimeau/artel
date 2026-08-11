#!/usr/bin/env python3
"""Does the suite catch silent degradation, or only crashes?

Nearly every bug found in this codebase during August 2026 produced a QUIET,
BELIEVABLE wrong answer rather than an exception: captures digested but never
acknowledged, an inert plugin reporting "0 tokens of overhead", an
unauthenticated CLI returning empty patches indistinguishable from failed fixes,
a docs anchor blessing a page that was already wrong.

Ordinary tests assert that things work. They rarely assert that a BROKEN
dependency is noticed, because the mock always behaves. So this reintroduces
each failure deliberately and asks whether anything goes red.

A surviving mutation is not hypothetical — it is a bug this repo could ship
again without noticing. When one survives: write the test, then re-run.

    uv run python scripts/sabotage.py

Restores every file it touches, including on failure. Run it on a clean tree.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MUTATIONS = [
    (
        "capture: acknowledge only after the whole batch (the original livelock)",
        "artel/archivist/compaction.py",
        """        try:
            await client.digest_captures([cap["id"]])
            digested.append(cap["id"])
        except Exception as e:
            log.warning("could not mark capture %s digested: %s", cap["id"][:8], e)""",
        """        digested.append(cap["id"])
    if digested:
        await client.digest_captures(digested)""",
    ),
    (
        "git check: pass when there is no baseline (degrade to allow)",
        "artel/server/git_anchor.py",
        '    if baseline is None:\n        return False, f"no baseline recorded for {anchor}; nothing to compare against"',
        '    if baseline is None:\n        return True, ""',
    ),
    (
        "done-check: unknown kind passes instead of failing",
        "artel/server/reactor.py",
        '''    fn = CHECKS.get(check.kind)
    if fn is None:
        return False, f"unknown done-check kind {check.kind!r}"''',
        '    fn = CHECKS.get(check.kind)\n    if fn is None:\n        return True, ""',
    ),
    (
        "sqlite action: drop the SELECT-only guard",
        "artel/server/reactor.py",
        """    if not rendered.lower().startswith("select"):
        raise ValueError("sqlite action must be a single SELECT")""",
        "    pass",
    ),
    (
        "recall hook: swallow every error and return nothing",
        "scripts/_artel_hooks.py",
        'def search(query, limit=6, project=""):',
        'def search(query, limit=6, project=""):\n    return []\n\ndef _unused_search(query, limit=6, project=""):',
    ),
    (
        "contract: accept any payload (validation becomes a no-op)",
        "artel/server/contract.py",
        "def validate_payload(contract: dict, payload: Any, path: str = _ROOT) -> list[str]:",
        "def validate_payload(contract: dict, payload: Any, path: str = _ROOT) -> list[str]:\n    return []\n\n\ndef _unused_validate(contract: dict, payload: Any, path: str = _ROOT) -> list[str]:",
    ),
    (
        "regret sensor: never record an event (controller goes blind)",
        "artel/server/routes/memory.py",
        "    for hid in hit_ids:\n        row = rows.get(hid) if isinstance(rows, dict) else None",
        "    for hid in []:\n        row = rows.get(hid) if isinstance(rows, dict) else None",
    ),
]


def run_suite() -> tuple[bool, str]:
    proc = subprocess.run(
        ["uv", "run", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    tail = [ln for ln in proc.stdout.splitlines() if "passed" in ln or "failed" in ln]
    return proc.returncode == 0, (tail[-1] if tail else "no summary")


def main() -> int:
    survived, caught = [], []
    for label, rel, old, new in MUTATIONS:
        path = ROOT / rel
        original = path.read_text()
        if old not in original:
            print(f"SKIP  {label}\n      (anchor text not found in {rel})")
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            green, summary = run_suite()
        finally:
            path.write_text(original)
        if green:
            survived.append(label)
            print(f"SURVIVED  {label}\n          suite still green: {summary}")
        else:
            caught.append(label)
            print(f"caught    {label}")
    print("\n" + "=" * 70)
    print(f"caught {len(caught)}/{len(caught) + len(survived)}")
    for label in survived:
        print(f"  SURVIVED: {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
