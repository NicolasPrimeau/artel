#!/usr/bin/env python3
"""Shared implementation for the Artel plugin hooks.

Invoked as: _artel_hooks.py <kind>   kind in {recall, gotcha, inbox, stop, status}.

Reads the hook JSON payload on stdin (where applicable), calls the Artel REST API
read-only, and prints the hook output. Config comes from
CLAUDE_PLUGIN_OPTION_ARTEL_URL / _AGENT_ID / _API_KEY, falling back to
ARTEL_URL / ARTEL_AGENT_ID / ARTEL_API_KEY (so the same module serves opencode and
plain-shell use). Never raises; a missing or down Artel server is silent.

Per-session dedup: each entry/message is surfaced at most once per session_id, so
the same memory or unread message does not re-inject on every prompt.
"""

import glob
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request

TIMEOUT = 3.0

ACKS = {
    "yes",
    "no",
    "ok",
    "okay",
    "yep",
    "sure",
    "continue",
    "go on",
    "go ahead",
    "proceed",
    "do it",
    "run it",
    "run the tests",
    "next",
    "stop",
    "thanks",
    "please continue",
    "keep going",
    "y",
    "n",
}


def _cfg(plugin_opt, env):
    return os.environ.get(plugin_opt) or os.environ.get(env) or ""


URL = _cfg("CLAUDE_PLUGIN_OPTION_ARTEL_URL", "ARTEL_URL").rstrip("/")
AID = _cfg("CLAUDE_PLUGIN_OPTION_AGENT_ID", "ARTEL_AGENT_ID")
KEY = _cfg("CLAUDE_PLUGIN_OPTION_API_KEY", "ARTEL_API_KEY")


def configured():
    return bool(URL and AID and KEY)


def get(path):
    req = urllib.request.Request(URL + path, headers={"x-agent-id": AID, "x-api-key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except Exception:
        return None


def search(query, limit=6):
    qs = urllib.parse.urlencode(
        {
            "q": query[:300],
            "limit": str(limit),
            "confidence_min": "0.5",
            "max_content_length": "300",
        }
    )
    result = get("/memory/search?" + qs)
    return result if isinstance(result, list) else []


def payload():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def clip(text, n):
    return " ".join(str(text or "").split())[:n]


def content_lower(entry):
    return " ".join(str(entry.get("content") or "").split()).lower()


def seen_filter(session_id, kind, ids):
    """Return the subset of ids not surfaced before this session; record them."""
    ids = [i for i in ids if i]
    if not session_id:
        return ids
    tmp = tempfile.gettempdir()
    now = time.time()
    for stale in glob.glob(os.path.join(tmp, "artel-seen-*")):
        try:
            if now - os.path.getmtime(stale) > 86400:
                os.remove(stale)
        except OSError:
            pass
    path = os.path.join(tmp, f"artel-seen-{kind}-{session_id}.txt")
    try:
        with open(path) as fh:
            prev = set(fh.read().split())
    except OSError:
        prev = set()
    fresh = [i for i in ids if i not in prev]
    if fresh:
        try:
            with open(path, "a") as fh:
                fh.write("\n".join(fresh) + "\n")
        except OSError:
            pass
    return fresh


def emit_context(event, context):
    print(
        json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}})
    )


def _msg_key(m):
    return str(m.get("id") or (str(m.get("from_agent")) + ":" + str(m.get("body"))))


def cmd_recall():
    data = payload()
    prompt = (data.get("prompt") or "").strip()
    if len(prompt) < 12 or prompt.lower().strip(" .!?") in ACKS:
        return
    results = [e for e in search(prompt, limit=6) if isinstance(e, dict)]
    if not results:
        return
    fresh = set(seen_filter(data.get("session_id", ""), "recall", [e.get("id") for e in results]))
    results = [e for e in results if e.get("id") in fresh]
    if not results:
        return
    memories = [e for e in results if e.get("type") != "skill"]
    skills = [e for e in results if e.get("type") == "skill"]
    parts = []
    if memories:
        parts.append(
            "Relevant memory:\n"
            + "\n".join("- " + clip(e.get("content"), 160) for e in memories[:2])
        )
    if skills:
        parts.append("Skill that may apply: " + clip(skills[0].get("content"), 160))
    if parts:
        emit_context("UserPromptSubmit", "[Artel] " + "\n".join(parts))


def cmd_gotcha():
    data = payload()
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    name = os.path.basename(path)
    if not name:
        return
    stem = os.path.splitext(name)[0]

    def about(entry):
        c = content_lower(entry)
        if name.lower() in c:
            return True
        if len(stem) >= 4 and stem.lower() in c:
            return True
        sp = str(entry.get("source_path") or "").lower()
        return sp.endswith("/" + name.lower()) or sp == name.lower()

    hits = [
        e for e in search((name + " " + stem).strip(), limit=4) if isinstance(e, dict) and about(e)
    ]
    if not hits:
        return
    keys = [name + ":" + str(e.get("id")) for e in hits]
    fresh = set(seen_filter(data.get("session_id", ""), "gotcha", keys))
    hits = [e for e in hits if (name + ":" + str(e.get("id"))) in fresh]
    if not hits:
        return
    lines = "\n".join("- " + clip(e.get("content"), 180) for e in hits[:2])
    emit_context("PreToolUse", "[Artel] Notes on " + name + " from shared memory:\n" + lines)


def _unread(data, kind):
    msgs = get("/messages/inbox")
    if not isinstance(msgs, list) or not msgs:
        return []
    fresh = set(seen_filter(data.get("session_id", ""), kind, [_msg_key(m) for m in msgs]))
    return [m for m in msgs if _msg_key(m) in fresh]


def cmd_inbox():
    data = payload()
    msgs = _unread(data, "inbox")
    if not msgs:
        return
    lines = " | ".join(
        str(m.get("from_agent", "?")) + ": " + str(m.get("body", "")) for m in msgs[:10]
    )
    emit_context("UserPromptSubmit", "[Artel] " + str(len(msgs)) + " new message(s): " + lines)


def cmd_stop():
    data = payload()
    if data.get("stop_hook_active"):
        return
    msgs = _unread(data, "stop")
    if not msgs:
        return
    lines = "\n".join(
        str(m.get("from_agent", "?")) + ": " + str(m.get("body", "")) for m in msgs[:10]
    )
    reason = (
        "[Artel] " + str(len(msgs)) + " unread message(s) arrived while you worked — "
        "handle or acknowledge them (mark read) before stopping:\n" + lines
    )
    print(json.dumps({"decision": "block", "reason": reason}))


def cmd_status():
    cache = os.path.join(tempfile.gettempdir(), f"artel-status-{AID or 'x'}.txt")
    try:
        if time.time() - os.path.getmtime(cache) < 10:
            sys.stdout.write(open(cache).read())
            return
    except OSError:
        pass
    tasks = get("/tasks?status=open")
    msgs = get("/messages/inbox")
    n_tasks = len(tasks) if isinstance(tasks, list) else 0
    n_msgs = len(msgs) if isinstance(msgs, list) else 0
    line = "⚡ artel"
    if n_tasks:
        line += f" · {n_tasks} task{'' if n_tasks == 1 else 's'}"
    if n_msgs:
        line += f" · {n_msgs} msg{'' if n_msgs == 1 else 's'}"
    try:
        with open(cache, "w") as fh:
            fh.write(line)
    except OSError:
        pass
    sys.stdout.write(line)


def main():
    if not configured():
        return
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = {
        "recall": cmd_recall,
        "gotcha": cmd_gotcha,
        "inbox": cmd_inbox,
        "stop": cmd_stop,
        "status": cmd_status,
    }.get(kind)
    if handler:
        try:
            handler()
        except Exception:
            pass


if __name__ == "__main__":
    main()
