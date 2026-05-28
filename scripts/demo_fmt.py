#!/usr/bin/env python3
"""
Pipe claude -p --output-format stream-json --verbose through this to show
Artel MCP tool calls and results in a clean demo format with a spinner
while Claude is thinking between tool calls.

Usage:
    claude -p "..." --output-format stream-json --verbose | python3 scripts/demo_fmt.py
"""

import json
import sys
import threading
import time

R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"

SKIP_TOOLS = {"ToolSearch"}
PREFIX = "mcp__artel__"

_spinner_active = False
_spinner_thread: threading.Thread | None = None
_pending_tool_name: str | None = None


def _start_spinner() -> None:
    global _spinner_active, _spinner_thread
    _spinner_active = True

    def _run() -> None:
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        i = 0
        while _spinner_active:
            sys.stdout.write(f"\r  {DIM}{frames[i]}  calling claude…{R}")
            sys.stdout.flush()
            time.sleep(0.12)
            i = (i + 1) % len(frames)

    _spinner_thread = threading.Thread(target=_run, daemon=True)
    _spinner_thread.start()


def _stop_spinner() -> None:
    global _spinner_active
    _spinner_active = False
    if _spinner_thread:
        _spinner_thread.join(timeout=0.5)
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def _short(text: str, n: int = 92) -> str:
    text = text.strip().replace("\n", " ")
    return text[:n] + "…" if len(text) > n else text


def _tool_label(name: str) -> str:
    return name[len(PREFIX) :] if name.startswith(PREFIX) else name


def handle(line: str) -> None:
    global _pending_tool_name
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return

    t = ev.get("type")

    if t == "assistant":
        msg = ev.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                if name in SKIP_TOOLS:
                    _pending_tool_name = None
                    return
                _stop_spinner()
                label = _tool_label(name)
                inp = block.get("input", {})
                extras = []
                if "tags" in inp:
                    tags = inp["tags"]
                    extras.append("[" + ", ".join(tags) + "]")
                if "title" in inp:
                    extras.append(f'"{inp["title"][:48]}"')
                extra = "  " + "  ".join(extras) if extras else ""
                print(f"\n  {BOLD}{CYAN}▶ {label}(){R}{DIM}{extra}{R}", flush=True)
                _pending_tool_name = name
                _start_spinner()

            elif block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    _stop_spinner()
                    print(f"  {DIM}{_short(text, 100)}{R}", flush=True)

    elif t == "user":
        msg = ev.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "tool_result" and _pending_tool_name:
                if _pending_tool_name in SKIP_TOOLS:
                    _pending_tool_name = None
                    return
                _stop_spinner()
                raw = block.get("content", "")
                if isinstance(raw, list):
                    raw = " ".join(c.get("text", "") for c in raw if isinstance(c, dict))
                try:
                    parsed = json.loads(raw)
                    result_text = parsed.get("result", str(parsed))
                except (json.JSONDecodeError, AttributeError):
                    result_text = str(raw)
                print(f"  {DIM}    └─ {_short(result_text, 92)}{R}", flush=True)
                _pending_tool_name = None


def main() -> None:
    _start_spinner()
    try:
        for line in sys.stdin:
            line = line.rstrip()
            if line:
                handle(line)
    finally:
        _stop_spinner()


if __name__ == "__main__":
    main()
