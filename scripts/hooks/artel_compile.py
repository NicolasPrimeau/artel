import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
).stdout.strip()
if _ROOT and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from artel.compile import compile_source  # noqa: E402


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def _changed_files(staged: bool) -> list[str]:
    if staged:
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    else:
        out = _git("ls-files")
    return [f for f in out.splitlines() if f]


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _units_for(files: list[str], exts: tuple[str, ...]) -> list[dict]:
    units: list[dict] = []
    for path in files:
        if not path.endswith(exts):
            continue
        src = _read(path)
        if src is None:
            continue
        for u in compile_source(path, src):
            units.append(
                {
                    "path": u.path,
                    "symbol": u.symbol,
                    "lang": u.lang,
                    "kind": u.kind,
                    "start_line": u.start_line,
                    "end_line": u.end_line,
                    "sha": u.sha,
                    "description": u.description,
                    "deps": [{"kind": d.kind, "name": d.name} for d in u.deps],
                }
            )
    return units


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    full = "--all" in argv
    exts = tuple(os.environ.get("ARTEL_COMPILE_EXTS", ".py").split(","))
    files = _changed_files(staged=not full)
    units = _units_for(files, exts)
    if not units:
        print("artel-compile: nothing to compile.", file=sys.stderr)
        return 0

    if dry:
        print(f"artel-compile (dry-run): {len(units)} unit(s) from {len(files)} changed file(s).")
        for u in units[:40]:
            print(f"  [{u['kind']}] {u['path']}:{u['symbol'] or '(module)'} sha={u['sha'][:10]}")
        return 0

    url = os.environ.get("ARTEL_URL", "http://localhost:8000").rstrip("/")
    agent = os.environ.get("ARTEL_AGENT_ID") or os.environ.get("MCP_AGENT_ID")
    key = os.environ.get("ARTEL_AGENT_KEY") or os.environ.get("MCP_AGENT_KEY")
    project = os.environ.get("ARTEL_PROJECT") or os.environ.get("MCP_PROJECT")
    if not agent or not key:
        print("artel-compile: ARTEL_AGENT_ID/KEY not set — skipping (no-op).", file=sys.stderr)
        return 0

    body = json.dumps(
        {"project": project, "commit": _git("rev-parse", "HEAD") or None, "units": units}
    ).encode()
    req = urllib.request.Request(
        f"{url}/compile",
        data=body,
        method="POST",
        headers={"content-type": "application/json", "x-agent-id": agent, "x-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            report = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"artel-compile: server unreachable ({e}); skipping (no-op).", file=sys.stderr)
        return 0

    print(
        f"artel-compile: {report.get('created', 0)} new, {report.get('updated', 0)} recompiled, "
        f"{report.get('unchanged', 0)} unchanged, {len(report.get('invalidated', []))} downstream "
        f"invalidated across {report.get('anchors', 0)} anchor(s).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
