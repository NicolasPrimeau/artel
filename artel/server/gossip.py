import asyncio
import json
import logging
import secrets

import httpx

from ..store.db import get_db
from .config import settings
from .feed_poller import _poll_feed
from .models import new_id

log = logging.getLogger(__name__)

_GOSSIP_INTERVAL = 300
_MAX_GOSSIP_LINKS = 16
_GOSSIP_AGENT = "gossip"


async def gossip_once() -> int:
    if not settings.gossip_enabled:
        return 0
    own = (settings.public_url or "").rstrip("/")
    if not own:
        return 0
    db = get_db()
    links = [
        dict(r)
        for r in db.execute(
            "SELECT p.peer_url, p.project, f.url AS feed_url FROM peer_links p"
            " JOIN feed_subscriptions f ON f.id=p.feed_id"
        ).fetchall()
    ]
    known = {link["peer_url"].rstrip("/") for link in links}
    auto = db.execute(
        "SELECT COUNT(*) FROM peer_links WHERE created_by=?", (_GOSSIP_AGENT,)
    ).fetchone()[0]
    added = 0
    for link in links:
        if auto + added >= _MAX_GOSSIP_LINKS:
            break
        if "/memory/feed.json" not in link["feed_url"]:
            continue
        gossip_url = link["feed_url"].replace("/memory/feed.json", "/mesh/gossip")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                resp = await client.get(gossip_url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
        except Exception:
            continue
        for peer in data.get("peers") or []:
            cand = (peer.get("peer_url") or "").rstrip("/")
            if not cand.startswith(("http://", "https://")):
                continue
            if cand == own or cand in known:
                continue
            if peer.get("project") != link["project"]:
                continue
            if await _adopt(db, cand, link["project"], link["peer_url"]):
                known.add(cand)
                added += 1
                if auto + added >= _MAX_GOSSIP_LINKS:
                    break
    return added


async def _adopt(db, peer_url: str, project: str | None, via: str) -> bool:
    from .routes.mesh import _create_peer_link

    own = (settings.public_url or "").rstrip("/")
    token_id = new_id()
    token = secrets.token_urlsafe(32)
    with db:
        db.execute(
            "INSERT INTO mesh_tokens (id, token, label, project, created_by) VALUES (?,?,?,?,?)",
            (token_id, token, f"gossip:{peer_url}", project, _GOSSIP_AGENT),
        )
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.post(
                f"{peer_url}/mesh/handshake",
                json={
                    "initiator_url": own,
                    "initiator_token": token,
                    "project": project,
                    "via": via,
                },
            )
            resp.raise_for_status()
            their_token = resp.json()["token"]
    except Exception as e:
        with db:
            db.execute("DELETE FROM mesh_tokens WHERE id=?", (token_id,))
        log.info("gossip adoption of %s (via %s) failed: %s", peer_url, via, e)
        return False

    _, feed_id = _create_peer_link(db, _GOSSIP_AGENT, peer_url, their_token, project)
    log.info("gossip: linked peer %s (vouched by %s)", peer_url, via)
    with db:
        db.execute(
            "INSERT INTO archivist_logs (id, level, source, action, message, details)"
            " VALUES (?,?,?,?,?,?)",
            (
                new_id(),
                "info",
                "gossip",
                "peer_linked",
                f"gossip discovered and linked peer {peer_url} (vouched by {via})",
                json.dumps({"peer_url": peer_url, "via": via, "project": project}),
            ),
        )
    feed_row = db.execute("SELECT * FROM feed_subscriptions WHERE id=?", (feed_id,)).fetchone()
    if feed_row:
        asyncio.create_task(_poll_feed(dict(feed_row)))
    return True


async def run_gossip() -> None:
    while True:
        await asyncio.sleep(_GOSSIP_INTERVAL)
        try:
            await gossip_once()
        except Exception as e:
            log.error("gossip cycle failed: %s", e)
