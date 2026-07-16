import json
from unittest.mock import MagicMock, patch

from tests.conftest import HEADERS, TEST_AGENT, TEST_KEY


def _make_owner(agent_id=TEST_AGENT):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    db.execute("UPDATE agents SET role='owner' WHERE id=?", (agent_id,))
    db.commit()


async def _link(client, peer="https://b.example.com", peer_token="tokB", project=None):
    _make_owner()
    r = await client.post(
        "/mesh/peers",
        json={"peer_url": peer, "peer_token": peer_token, "project": project},
        headers=HEADERS,
    )
    assert r.status_code == 201
    return r


def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json = MagicMock(return_value=payload)
    m.raise_for_status = MagicMock()
    m.text = json.dumps(payload)
    m.headers = {"content-type": "application/json"}
    return m


# --- /mesh/gossip ----------------------------------------------------------------------


async def test_gossip_lists_peers_without_leaking_tokens(client):
    await _link(client, peer="https://b.example.com", peer_token="supersecret-tok")
    r = await client.get("/mesh/gossip", params={"agent_id": TEST_AGENT, "api_key": TEST_KEY})
    assert r.status_code == 200
    data = r.json()
    assert data["peers"] == [{"peer_url": "https://b.example.com", "project": None}]
    assert "supersecret-tok" not in json.dumps(data)


async def test_gossip_requires_auth(client):
    r = await client.get("/mesh/gossip")
    assert r.status_code in (401, 403)


# --- vouched handshake -----------------------------------------------------------------


async def test_handshake_accepts_wan_peer_vouched_by_mutual_peer(client, monkeypatch):
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    monkeypatch.setattr(cfg_mod.settings, "mdns_enabled", False)
    await _link(client, peer="https://b.example.com", peer_token="tokB")

    def route(url, **kw):
        assert "/mesh/gossip" in str(url) and "mesh_token=tokB" in str(url)
        return _resp(200, {"peers": [{"peer_url": "https://c.example.com", "project": None}]})

    with patch("httpx.AsyncClient.get", side_effect=route):
        r = await client.post(
            "/mesh/handshake",
            json={
                "initiator_url": "https://c.example.com",
                "initiator_token": "tokC",
                "via": "https://b.example.com",
            },
        )
    assert r.status_code == 200
    assert len(r.json()["token"]) > 16
    db = db_mod.get_db()
    assert db.execute("SELECT 1 FROM peer_links WHERE peer_url='https://c.example.com'").fetchone()


async def test_handshake_rejects_unvouched_wan_peer(client, monkeypatch):
    import artel.server.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "mdns_enabled", False)
    await _link(client, peer="https://b.example.com", peer_token="tokB")

    with patch("httpx.AsyncClient.get", return_value=_resp(200, {"peers": []})):
        r = await client.post(
            "/mesh/handshake",
            json={
                "initiator_url": "https://evil.example.com",
                "initiator_token": "tokE",
                "via": "https://b.example.com",
            },
        )
    assert r.status_code == 403


async def test_handshake_rejects_via_unknown_voucher(client, monkeypatch):
    import artel.server.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "mdns_enabled", False)
    r = await client.post(
        "/mesh/handshake",
        json={
            "initiator_url": "https://c.example.com",
            "initiator_token": "tokC",
            "via": "https://never-linked.example.com",
        },
    )
    assert r.status_code == 403


# --- gossip adoption loop ----------------------------------------------------------------


async def test_gossip_once_adopts_vouched_peer(client, monkeypatch):
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod
    from artel.server import gossip

    monkeypatch.setattr(cfg_mod.settings, "public_url", "https://a.example.com")
    await _link(client, peer="https://b.example.com", peer_token="tokB")

    def on_get(url, **kw):
        u = str(url)
        if "/mesh/gossip" in u:
            return _resp(200, {"peers": [{"peer_url": "https://c.example.com", "project": None}]})
        return _resp(200, {"version": "https://jsonfeed.org/version/1.1", "items": []})

    def on_post(url, **kw):
        assert str(url) == "https://c.example.com/mesh/handshake"
        body = kw.get("json") or {}
        assert body["initiator_url"] == "https://a.example.com"
        assert body["via"] == "https://b.example.com"
        return _resp(200, {"token": "tokFromC"})

    with (
        patch("httpx.AsyncClient.get", side_effect=on_get),
        patch("httpx.AsyncClient.post", side_effect=on_post),
    ):
        added = await gossip.gossip_once()
    assert added == 1
    db = db_mod.get_db()
    link = db.execute(
        "SELECT p.created_by, f.url FROM peer_links p JOIN feed_subscriptions f ON f.id=p.feed_id"
        " WHERE p.peer_url='https://c.example.com'"
    ).fetchone()
    assert link is not None
    assert link["created_by"] == "gossip"
    assert "mesh_token=tokFromC" in link["url"]

    with (
        patch("httpx.AsyncClient.get", side_effect=on_get),
        patch("httpx.AsyncClient.post", side_effect=on_post),
    ):
        assert await gossip.gossip_once() == 0  # already known — idempotent


async def test_gossip_once_skips_self_and_project_mismatch(client, monkeypatch):
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod
    from artel.server import gossip

    monkeypatch.setattr(cfg_mod.settings, "public_url", "https://a.example.com")
    await _link(client, peer="https://b.example.com", peer_token="tokB")

    def on_get(url, **kw):
        if "/mesh/gossip" in str(url):
            return _resp(
                200,
                {
                    "peers": [
                        {"peer_url": "https://a.example.com", "project": None},  # ourselves
                        {"peer_url": "https://d.example.com", "project": "otherproj"},
                    ]
                },
            )
        return _resp(200, {"version": "https://jsonfeed.org/version/1.1", "items": []})

    with patch("httpx.AsyncClient.get", side_effect=on_get):
        assert await gossip.gossip_once() == 0
    assert (
        db_mod.get_db().execute("SELECT COUNT(*) FROM peer_links").fetchone()[0] == 1
    )  # only the original link


async def test_gossip_once_noop_without_public_url(client, monkeypatch):
    import artel.server.config as cfg_mod
    from artel.server import gossip

    monkeypatch.setattr(cfg_mod.settings, "public_url", "")
    await _link(client)
    assert await gossip.gossip_once() == 0


async def test_gossip_once_disabled_by_setting(client, monkeypatch):
    import artel.server.config as cfg_mod
    from artel.server import gossip

    monkeypatch.setattr(cfg_mod.settings, "public_url", "https://a.example.com")
    monkeypatch.setattr(cfg_mod.settings, "gossip_enabled", False)
    await _link(client)
    assert await gossip.gossip_once() == 0


async def test_failed_handshake_rolls_back_minted_token(client, monkeypatch):
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod
    from artel.server import gossip

    monkeypatch.setattr(cfg_mod.settings, "public_url", "https://a.example.com")
    await _link(client, peer="https://b.example.com", peer_token="tokB")

    def on_get(url, **kw):
        if "/mesh/gossip" in str(url):
            return _resp(200, {"peers": [{"peer_url": "https://c.example.com", "project": None}]})
        return _resp(200, {"items": []})

    def on_post(url, **kw):
        raise RuntimeError("peer unreachable")

    with (
        patch("httpx.AsyncClient.get", side_effect=on_get),
        patch("httpx.AsyncClient.post", side_effect=on_post),
    ):
        assert await gossip.gossip_once() == 0
    db = db_mod.get_db()
    assert (
        db.execute("SELECT COUNT(*) FROM mesh_tokens WHERE created_by='gossip'").fetchone()[0] == 0
    )
    assert not db.execute(
        "SELECT 1 FROM peer_links WHERE peer_url='https://c.example.com'"
    ).fetchone()
