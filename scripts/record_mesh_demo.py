#!/usr/bin/env python3
"""
Record a real Playwright browser demo of the Artel Mesh tab.

Starts two Artel instances as subprocesses (ports 8101/8102), drives the UI
with Playwright, converts the Chromium-recorded video to docs/mesh_network.gif.

Usage:
    uv run python scripts/record_mesh_demo.py
"""

import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).parent.parent
FFMPEG = Path("/tmp/ffmpeg-7.0.2-amd64-static/ffmpeg")
CHROMIUM = Path.home() / ".cache/ms-playwright/chromium-1224/chrome-linux64/chrome"
OUT_GIF = REPO / "docs/mesh_network2.gif"

PORT_A, PORT_B = 8101, 8102
UI_PW = "artel-demo-2026"


# ── helpers ───────────────────────────────────────────────────────────────────


def _wait_up(port: int, timeout: int = 30):
    import http.client

    for _ in range(timeout * 5):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request("GET", "/agents")
            resp = conn.getresponse()
            if resp.status in (200, 401, 403):
                return
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        time.sleep(0.2)
    raise RuntimeError(f":{port} never came up")


def _api(port: int, method: str, path: str, data=None, *, agent_id="", api_key="", reg_key=""):
    url = f"http://127.0.0.1:{port}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"content-type": "application/json"}
    if agent_id:
        headers["x-agent-id"] = agent_id
        headers["x-api-key"] = api_key
    if reg_key:
        headers["x-registration-key"] = reg_key
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


UI_AGENT_ID = "demo-ui"


def _ui_key(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT api_key FROM agents WHERE id=?", (UI_AGENT_ID,)).fetchone()
    conn.close()
    return row[0] if row else ""


def _start(port: int, db_path: str, reg_key: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "DB_PATH": db_path,
        "PORT": str(port),
        "REGISTRATION_KEY": reg_key,
        "UI_PASSWORD": UI_PW,
        "MDNS_ENABLED": "false",
        "PUBLIC_URL": f"http://127.0.0.1:{port}",
        "UI_AGENT_ID": UI_AGENT_ID,
    }
    return subprocess.Popen(
        [sys.executable, "-m", "artel.server"],
        env=env,
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ── recording ─────────────────────────────────────────────────────────────────


def _record(tmp: Path, db_a: str, db_b: str):
    from playwright.sync_api import sync_playwright

    key_a = _ui_key(db_a)
    key_b = _ui_key(db_b)
    print(f"artel-ui key A: {key_a[:8]}…  B: {key_b[:8]}…")

    # Pre-create a token on B so A can subscribe to B's feed
    tok_b = _api(
        PORT_B,
        "POST",
        "/mesh/tokens",
        {"label": "for-A", "project": None},
        agent_id=UI_AGENT_ID,
        api_key=key_b,
    )
    print(f"pre-created token on B: {tok_b['token'][:12]}…")

    video_dir = tmp / "video"
    video_dir.mkdir()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=str(CHROMIUM),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            slow_mo=80,
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 800},
        )

        def login(page, port):
            page.goto(f"http://127.0.0.1:{port}/ui/login")
            page.wait_for_load_state("networkidle")
            page.locator("input[type=password]").fill(UI_PW)
            page.locator("button[type=submit]").click()
            page.wait_for_url("**/ui", timeout=8000)
            page.wait_for_load_state("networkidle")
            time.sleep(0.6)

        def click_tab(page, label):
            page.locator(f"button.nav-btn:has-text('{label}')").click()
            time.sleep(1.0)

        # ── Instance A ────────────────────────────────────────────────────────
        page_a = ctx.new_page()
        print("logging into A …")
        login(page_a, PORT_A)
        time.sleep(0.6)

        click_tab(page_a, "Mesh")
        page_a.wait_for_selector("#mesh-token-section", timeout=6000)
        time.sleep(1.5)

        # Generate a token on A (the "my mesh token" panel on the left)
        print("generating token on A …")
        print("page title:", page_a.title())
        print("page url:", page_a.url)
        print("mesh-token-section html:", page_a.locator("#mesh-token-section").inner_html())
        page_a.on("dialog", lambda d: d.accept(""))
        gen = page_a.locator("button:has-text('generate token')").first
        gen.click()

        # Two prompts fire (label, project); handler accepts both with "".
        # After POST /mesh/tokens + loadMesh(), the token appears in
        # #mesh-token-section code (inline element with the raw token string).
        token_el = page_a.locator("#mesh-token-section code")
        token_el.wait_for(state="visible", timeout=12000)
        for _ in range(30):
            token_a = token_el.first.inner_text().strip()
            if token_a:
                break
            time.sleep(0.2)
        print(f"token A: {token_a[:14]}…")
        time.sleep(1.0)

        # Scroll to show the "linked peers" panel and fill in peer B's details
        page_a.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.6)
        page_a.locator("#mesh-url").fill(f"http://127.0.0.1:{PORT_B}")
        time.sleep(0.5)
        page_a.locator("#mesh-token").fill(tok_b["token"])
        time.sleep(0.5)
        page_a.locator("button:has-text('link peer')").first.click()
        time.sleep(2.0)

        # ── Instance B ────────────────────────────────────────────────────────
        page_b = ctx.new_page()
        print("logging into B …")
        login(page_b, PORT_B)
        time.sleep(0.5)

        click_tab(page_b, "Mesh")
        page_b.wait_for_selector("#mesh-token-section", timeout=6000)
        time.sleep(1.2)

        # Link B → A using A's generated token
        page_b.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        page_b.locator("#mesh-url").fill(f"http://127.0.0.1:{PORT_A}")
        time.sleep(0.4)
        page_b.locator("#mesh-token").fill(token_a)
        time.sleep(0.4)
        page_b.locator("button:has-text('link peer')").first.click()
        time.sleep(2.0)

        # Show B's peer list (now has A)
        page_b.evaluate("window.scrollTo(0, 0)")
        time.sleep(1.5)

        # ── Back to A: write a memory entry ─────────────────────────────────
        print("writing memory on A …")
        page_a.bring_to_front()
        click_tab(page_a, "Memory")

        # Find and fill the new-memory textarea/input
        content = page_a.locator("#new-content").first
        if content.count():
            content.fill("Rate limiter deployed — p99 latency down 40%")
            time.sleep(0.4)
            page_a.locator("button:has-text('save')").first.click()
            time.sleep(1.2)

        time.sleep(1.0)

        # ── Back to A Mesh tab to show both peers ────────────────────────────
        click_tab(page_a, "Mesh")
        page_a.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2.5)

        ctx.close()
        browser.close()

    _to_gif(video_dir)


def _to_gif(video_dir: Path):
    videos = sorted(video_dir.glob("*.webm"))
    if not videos:
        sys.exit("ERROR: no video recorded")
    video = videos[-1]
    print(f"converting {video.name} …")
    tmp_dir = video.parent
    palette = tmp_dir / "palette.png"

    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(video),
            "-vf",
            "fps=8,scale=900:-1:flags=lanczos,palettegen=stats_mode=diff",
            str(palette),
        ],
        check=True,
        capture_output=True,
    )

    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(video),
            "-i",
            str(palette),
            "-lavfi",
            "fps=8,scale=900:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer",
            str(OUT_GIF),
        ],
        check=True,
        capture_output=True,
    )

    print(f"wrote {OUT_GIF}  ({OUT_GIF.stat().st_size / 1024 / 1024:.1f} MB)")


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / ".cache/ms-playwright")

    tmp = Path(tempfile.mkdtemp())
    db_a = str(tmp / "a.db")
    db_b = str(tmp / "b.db")
    reg_key = secrets.token_hex(8)

    print(f"starting A :{PORT_A}  B :{PORT_B} …")
    proc_a = _start(PORT_A, db_a, reg_key)
    proc_b = _start(PORT_B, db_b, reg_key)

    try:
        _wait_up(PORT_A)
        _wait_up(PORT_B)
        print("both up")
        time.sleep(1.0)  # let lifespan settle
        _record(tmp, db_a, db_b)
    finally:
        proc_a.terminate()
        proc_b.terminate()
        proc_a.wait()
        proc_b.wait()


if __name__ == "__main__":
    main()
