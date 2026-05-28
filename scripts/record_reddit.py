#!/usr/bin/env python3
"""
Record the Reddit demo: real Claude Code session continuity story.

Three real claude sessions recorded via PTY, combined with act title cards
into a 3-act script: discover -> hand off -> fix.

Usage:
    python3 scripts/record_reddit.py
"""

import fcntl
import json
import os
import select
import signal
import struct
import sys
import termios
import time

COLS = 110
ROWS = 35
NIMBUS_DIR = "/home/nprimeau/projects/Nimbus"
MCP_CONFIG = "/home/nprimeau/projects/Nimbus/.mcp.json"
FINAL_GIF = "/home/nprimeau/projects/artel/docs/reddit.gif"

ACT_CARD_S = 4.5
FINALE_S = 4.0
CREDITS_S = 5.0
ACT_PAUSE_S = 3.0

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
YELLOW = "\x1b[33m"
WHITE = "\x1b[97m"
GREEN = "\x1b[32m"
GOLD = "\x1b[38;5;220m"
R = "\x1b[0m"

SESSION1_PROMPT = (
    "You are a Claude Code agent wrapping up work on the Nimbus project for the day. "
    "First, load your session context to catch up on anything from earlier sessions. "
    "You have just finished investigating a production bug: the BuildData geocoder hits "
    "Google Maps 429 rate-limit errors at peak load, and you confirmed OSM Nominatim "
    "handles bulk geocoding cleanly. Before you close the terminal: write that finding to "
    "memory so it is not lost, create a task to make the fix, and save a session handoff. "
    "Do not write or run any code — just record what you found and wrap up. Keep it brief."
)

SESSION2_PROMPT = (
    "You are opening a brand-new Claude Code session on the Nimbus project — a cold start, "
    "you remember nothing from before. Get oriented: load your session context to see what "
    "the last session left behind, list the open tasks, and claim the one that is waiting. "
    "Pull up any memory related to it so you know the details. "
    "Do not start implementing anything — just get up to speed and claim the work. "
    "Keep it brief."
)

SESSION3_PROMPT = (
    "You are opening a fresh Claude Code session on the Nimbus project — another cold start. "
    "Load your session context and check the task you have claimed. "
    "The geocoder fix has now been implemented and verified — switching batch geocoding to "
    "OSM Nominatim cleared the 429 errors. Close the loop: mark the task complete with a "
    "short note on what shipped, write a memory capturing the outcome, and save a final "
    "session handoff. Do not write code — just record the result. Keep it brief."
)


def _title_card(n: int, title: str, subtitle: str, duration: float) -> list:
    rule = "─" * COLS
    mid = ROWS // 2
    act_text = f"── ACT {n} ──"
    act_pad = (COLS - len(act_text)) // 2
    title_pad = (COLS - len(title)) // 2
    sub_pad = (COLS - len(subtitle)) // 2

    frame = "\x1b[2J\x1b[H"
    frame += f"\x1b[{mid - 3};1H{DIM}{rule}{R}"
    frame += f"\x1b[{mid - 1};{act_pad + 1}H{BOLD}{GOLD}{act_text}{R}"
    frame += f"\x1b[{mid + 1};{title_pad + 1}H{BOLD}{WHITE}{title}{R}"
    frame += f"\x1b[{mid + 2};{sub_pad + 1}H{WHITE}{subtitle}{R}"
    frame += f"\x1b[{mid + 4};1H{DIM}{rule}{R}"
    return [(0.0, "o", frame), (duration, "o", "\x1b[2J\x1b[H")]


def _finale_card(duration: float) -> list:
    mid = ROWS // 2
    lines = [
        f"  {BOLD}{WHITE}╔══════════════════════════════════════════════╗{R}",
        f"  {BOLD}{WHITE}║  {GREEN}zero cold starts{WHITE}                             ║{R}",
        f"  {BOLD}{WHITE}║  {DIM}memory · tasks · handoffs between sessions{WHITE}   {R}{BOLD}{WHITE}║{R}",
        f"  {BOLD}{WHITE}║  {DIM}one line in .mcp.json — any agent, any host{WHITE}  {R}{BOLD}{WHITE}║{R}",
        f"  {BOLD}{WHITE}╚══════════════════════════════════════════════╝{R}",
    ]
    frame = "\x1b[2J\x1b[H"
    start = mid - len(lines) // 2
    for i, line in enumerate(lines):
        frame += f"\x1b[{start + i};1H{line}"
    return [(0.0, "o", frame), (duration, "o", "")]


def _credits_card(duration: float) -> list:
    rule = "─" * COLS
    mid = ROWS // 2
    label = "── FIN ──"
    label_pad = (COLS - len(label)) // 2
    directed = "directed by"
    name = "Claudin Tarantino"
    directed_pad = (COLS - len(directed)) // 2
    name_pad = (COLS - len(name)) // 2

    frame = "\x1b[2J\x1b[H"
    frame += f"\x1b[{mid - 3};1H{DIM}{rule}{R}"
    frame += f"\x1b[{mid - 1};{label_pad + 1}H{BOLD}{GOLD}{label}{R}"
    frame += f"\x1b[{mid + 1};{directed_pad + 1}H{BOLD}{GOLD}{directed}{R}"
    frame += f"\x1b[{mid + 2};{name_pad + 1}H{BOLD}{WHITE}{name}{R}"
    frame += f"\x1b[{mid + 4};1H{DIM}{rule}{R}"
    return [(0.0, "o", frame), (duration, "o", "")]


def _set_pty_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _send_keys(master: int, text: str) -> None:
    chunk = 32
    for i in range(0, len(text), chunk):
        os.write(master, text[i : i + chunk].encode())
        time.sleep(0.04)
    os.write(master, b"\r")


TERMINAL_RESPONSES = [
    (b"\x1b[c", b"\x1b[?1;2c"),
    (b"\x1b[0c", b"\x1b[?1;2c"),
    (b"\x1b[>c", b"\x1b[>0;276;0c"),
    (b"\x1b[>0c", b"\x1b[>0;276;0c"),
]


def record_session(
    prompt: str,
    label: str,
    startup_wait: float = 12.0,
    idle_cutoff: float = 22.0,
    max_total: float = 180.0,
) -> list:
    print(f"  Recording {label}…", flush=True)
    master, slave = os.openpty()
    _set_pty_size(master, ROWS, COLS)
    _set_pty_size(slave, ROWS, COLS)

    pid = os.fork()
    if pid == 0:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        for fd in range(3, 256):
            try:
                os.close(fd)
            except OSError:
                pass
        os.chdir(NIMBUS_DIR)
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = str(COLS)
        env["LINES"] = str(ROWS)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        os.execvpe(
            "claude",
            [
                "claude",
                "--dangerously-skip-permissions",
                "--bare",
                "--mcp-config",
                MCP_CONFIG,
            ],
            env,
        )
        sys.exit(1)

    os.close(slave)
    events: list = []
    t0 = time.time()
    prompt_sent = False
    last_data = time.time()
    deadline = time.time() + max_total
    trust_dismissed = False

    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.05)
        if r:
            try:
                data = os.read(master, 65536)
                if not data:
                    break
                events.append(
                    (round(time.time() - t0, 4), "o", data.decode("utf-8", errors="replace"))
                )
                for q, resp in TERMINAL_RESPONSES:
                    if q in data:
                        try:
                            os.write(master, resp)
                        except OSError:
                            pass
                if not trust_dismissed and not prompt_sent:
                    text = data.decode("utf-8", errors="replace")
                    if "trust" in text.lower() or (
                        "enter" in text.lower() and "folder" in text.lower()
                    ):
                        time.sleep(0.3)
                        os.write(master, b"\r")
                        trust_dismissed = True
                last_data = time.time()
            except OSError:
                break
        else:
            elapsed = time.time() - t0
            if not prompt_sent and elapsed >= startup_wait:
                print(f"    sending prompt at t={elapsed:.1f}s", flush=True)
                _send_keys(master, prompt)
                prompt_sent = True
                last_data = time.time()
            if prompt_sent and (time.time() - last_data) > idle_cutoff:
                print(f"    {idle_cutoff}s idle — done", flush=True)
                break

    drain_deadline = time.time() + 6.0
    while time.time() < drain_deadline:
        r, _, _ = select.select([master], [], [], 0.3)
        if not r:
            break
        try:
            data = os.read(master, 65536)
            if data:
                events.append(
                    (round(time.time() - t0, 4), "o", data.decode("utf-8", errors="replace"))
                )
        except OSError:
            break

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        pass
    try:
        os.close(master)
    except OSError:
        pass

    dur = events[-1][0] if events else 0
    print(f"    → {len(events)} events, {dur:.1f}s", flush=True)
    return events


def _compress_idle(events: list, max_gap: float) -> list:
    if len(events) < 2:
        return events
    out = [events[0]]
    drift = 0.0
    for i in range(1, len(events)):
        gap = events[i][0] - events[i - 1][0]
        if gap > max_gap:
            drift += gap - max_gap
        out.append((round(events[i][0] - drift, 4), events[i][1], events[i][2]))
    return out


ACTS = [
    (1, "THE DISCOVERY", "session one — a bug is found, and written down"),
    (2, "THE HANDOFF", "session two — cold start — the work is claimed"),
    (3, "THE FIX", "session three — cold start — the loop is closed"),
]


def combine(sessions: list, out: str) -> None:
    t = 0.0
    combined = []

    def _shift(events: list, offset: float) -> list:
        return [(round(ev[0] + offset, 4), ev[1], ev[2]) for ev in events]

    for (n, title, subtitle), session in zip(ACTS, sessions):
        card = _title_card(n, title, subtitle, ACT_CARD_S)
        combined += _shift(card, t)
        t += card[-1][0] + 0.1

        session = _compress_idle(session, 1.5)
        combined += _shift(session, t)
        t += (session[-1][0] if session else 0) + ACT_PAUSE_S

    finale = _finale_card(FINALE_S)
    combined += _shift(finale, t)
    t += FINALE_S + 0.1

    credits = _credits_card(CREDITS_S)
    combined += _shift(credits, t)
    t += CREDITS_S

    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": "Artel — session continuity",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "duration": t,
    }

    with open(out, "w") as f:
        f.write(json.dumps(header) + "\n")
        for row in combined:
            f.write(json.dumps(list(row)) + "\n")

    print(f"  → {out}: {len(combined)} events, {t:.1f}s total", flush=True)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/artel-reddit.cast"

    sessions = []
    for i, prompt in enumerate([SESSION1_PROMPT, SESSION2_PROMPT, SESSION3_PROMPT], 1):
        print(f"Session {i}:", flush=True)
        sessions.append(record_session(prompt, f"session {i}"))

    print("Combining…", flush=True)
    combine(sessions, out)

    raw_gif = "/tmp/artel-reddit-raw.gif"
    flatten = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flatten_gif.py")

    print("Rendering GIF…", flush=True)
    os.system(f"agg {out} {raw_gif} --speed 1.2 --font-size 13 --theme github-dark")

    print("Flattening (every frame full + opaque)…", flush=True)
    os.system(f"python3 {flatten} {raw_gif} {FINAL_GIF}")
    print("Done.", flush=True)
