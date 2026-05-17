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
OUT_GIF = REPO / "docs/mesh_network3.gif"

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
        "MDNS_ENABLED": "true",
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

    print("waiting for mDNS propagation …")
    time.sleep(6)

    video_dir = tmp / "video"
    video_dir.mkdir()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=str(CHROMIUM),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            slow_mo=120,
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 800},
        )

        def annotate(page, text):
            """Inject a floating caption banner at the bottom of the page."""
            escaped = text.replace("'", "\\'")
            page.evaluate(f"""(() => {{
                let b = document.getElementById('__demo_banner');
                if (!b) {{
                    b = document.createElement('div');
                    b.id = '__demo_banner';
                    b.style.cssText = `
                        position: fixed; bottom: 0; left: 0; right: 0; z-index: 99999;
                        background: rgba(0,0,0,0.82); color: #fff;
                        font: 600 15px/1 "SF Mono", monospace;
                        padding: 12px 20px; letter-spacing: .02em;
                        border-top: 2px solid #4a9eff;
                    `;
                    document.body.appendChild(b);
                }}
                b.textContent = '{escaped}';
            }})()""")

        def login(page, port, label):
            annotate(page, f"instance {label}  ·  logging in …")
            page.goto(f"http://127.0.0.1:{port}/ui/login")
            page.wait_for_load_state("networkidle")
            time.sleep(0.8)
            page.locator("input[type=password]").fill(UI_PW)
            time.sleep(0.5)
            page.locator("button[type=submit]").click()
            page.wait_for_url("**/ui", timeout=8000)
            page.wait_for_load_state("networkidle")
            time.sleep(1.0)

        def click_tab(page, label):
            page.locator(f"button.nav-btn:has-text('{label}')").click()
            time.sleep(1.2)

        # ── Instance A ───────────────────────────────────────────────────────
        page_a = ctx.new_page()
        login(page_a, PORT_A, "A  (port 8101)")

        annotate(page_a, "instance A  ·  opening Mesh tab")
        click_tab(page_a, "Mesh")
        page_a.wait_for_selector("#mesh-token-section", timeout=6000)
        time.sleep(2.0)

        # Poll until B appears in the discovered section (mDNS may take a moment)
        print("waiting for B to appear in A's discovered list …")
        annotate(page_a, "instance A  ·  waiting for instance B to appear via mDNS …")
        discovered_link = page_a.locator("#mesh-discovered-section button:has-text('link')")
        for _ in range(50):
            if discovered_link.count():
                break
            time.sleep(0.3)
            page_a.reload()
            page_a.wait_for_load_state("networkidle")
            page_a.locator("button.nav-btn:has-text('Mesh')").click()
            page_a.wait_for_selector("#mesh-token-section", timeout=4000)
            time.sleep(0.6)
        print(
            f"discovered section: {page_a.locator('#mesh-discovered-section').inner_html()[:140]}"
        )

        annotate(page_a, "instance A  ·  instance B discovered on LAN — no URL needed")
        time.sleep(3.0)

        # One-click link via mDNS handshake
        annotate(page_a, "instance A  ·  clicking Link — mutual handshake happens automatically")
        time.sleep(1.5)
        page_a.on("dialog", lambda d: d.accept(""))
        discovered_link.first.click()
        time.sleep(3.5)
        annotate(
            page_a, "instance A  ·  linked!  both instances now subscribe to each other's feed"
        )
        print("linked!")
        time.sleep(3.0)

        # ── Instance B: show its Mesh tab (A now appears as linked peer) ──────
        page_b = ctx.new_page()
        login(page_b, PORT_B, "B  (port 8102)")

        annotate(page_b, "instance B  ·  Mesh tab — A is already linked (handshake was mutual)")
        click_tab(page_b, "Mesh")
        page_b.wait_for_selector("#mesh-list", timeout=6000)
        time.sleep(3.5)

        # ── Instance A: write a memory entry ─────────────────────────────────
        print("writing memory on A …")
        page_a.bring_to_front()
        annotate(page_a, "instance A  ·  writing a memory entry")
        click_tab(page_a, "Memory")
        time.sleep(1.0)

        page_a.locator("#mc").fill("Rate limiter deployed — p99 latency down 40%")
        time.sleep(1.5)
        page_a.locator("button:has-text('write')").click()
        time.sleep(2.0)
        annotate(page_a, "instance A  ·  memory saved")
        print("memory written on A")
        time.sleep(2.0)

        # ── Instance B: Mesh tab → sync (B subscribes to A's feed) ──────────
        # B's peer link points to A's feed, so syncing on B pulls A's memories into B.
        page_b.bring_to_front()
        annotate(page_b, "instance B  ·  Mesh tab → Sync — pulling A's feed into B now")
        click_tab(page_b, "Mesh")
        page_b.wait_for_selector("#mesh-list", timeout=6000)
        time.sleep(1.5)
        sync_btn = page_b.locator("button:has-text('sync')").first
        sync_btn.click()
        time.sleep(3.0)
        annotate(page_b, "instance B  ·  sync complete — A's memory has been replicated")
        print("synced A→B feed")
        time.sleep(2.0)

        # ── Instance B: Memory tab shows the replicated entry ─────────────────
        annotate(page_b, "instance B  ·  Memory tab — entry from A is now here")
        click_tab(page_b, "Memory")
        time.sleep(4.0)
        print("showing B memory tab with replicated entry")

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
