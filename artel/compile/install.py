import inspect

from . import anchors

_RUNNER = """

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def _changed(staged):
    flt = ["diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else ["ls-files"]
    return [f for f in _git(*flt).splitlines() if f]


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def main(argv):
    dry = "--dry-run" in argv
    exts = tuple(os.environ.get("ARTEL_COMPILE_EXTS", ".py").split(","))
    files = _changed(staged="--all" not in argv)
    units = []
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
    if not units:
        print("artel-compile: nothing to compile.", file=sys.stderr)
        return 0
    if dry:
        print(f"artel-compile (dry-run): {len(units)} unit(s) from {len(files)} file(s).")
        for u in units[:40]:
            print(f"  [{u['kind']}] {u['path']}:{u['symbol'] or '(module)'} sha={u['sha'][:10]}")
        return 0
    url = os.environ.get("ARTEL_URL", "__ARTEL_URL__").rstrip("/")
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
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"artel-compile: server unreachable ({exc}); skipping (no-op).", file=sys.stderr)
        return 0
    print(
        f"artel-compile: {report.get('created', 0)} new, {report.get('updated', 0)} recompiled, "
        f"{report.get('unchanged', 0)} unchanged, {len(report.get('invalidated', []))} invalidated.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
"""

_INSTALL = """#!/bin/sh
set -e
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "artel: not inside a git repo" >&2; exit 1; }
HOOKS="$ROOT/.git/hooks"
mkdir -p "$HOOKS"
curl -fsSL "__ARTEL_URL__/compile/hook.py" -o "$HOOKS/artel_compile.py"
HOOK="$HOOKS/pre-commit"
LINE='python3 "$(git rev-parse --show-toplevel)/.git/hooks/artel_compile.py" || true'
if [ ! -f "$HOOK" ]; then
  printf '#!/bin/sh\\n%s\\n' "$LINE" > "$HOOK"
elif ! grep -q artel_compile "$HOOK"; then
  printf '%s\\n' "$LINE" >> "$HOOK"
fi
chmod +x "$HOOK" "$HOOKS/artel_compile.py"
echo "artel compile-mode hook installed -> $HOOK"
echo "set ARTEL_AGENT_ID + ARTEL_AGENT_KEY (or MCP_AGENT_ID/KEY) to enable; ARTEL_URL defaults to __ARTEL_URL__; without creds the hook is a safe no-op"
"""


def standalone_hook(artel_url: str) -> str:
    return inspect.getsource(anchors) + _RUNNER.replace("__ARTEL_URL__", artel_url)


def installer_sh(artel_url: str) -> str:
    return _INSTALL.replace("__ARTEL_URL__", artel_url)
