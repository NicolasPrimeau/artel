import pytest

from .conftest import HEADERS

UI_HDR = {"x-ui-session": "1"}


@pytest.fixture(autouse=True)
def ui_password(monkeypatch):
    import artel.server.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "ui_password", "secret")


async def _login(client):
    r = await client.post("/ui/login", data={"password": "secret"}, follow_redirects=False)
    assert r.status_code == 303
    return r.cookies["session"]


@pytest.mark.asyncio
async def test_owner_key_not_embedded_in_page(client):
    await _login(client)
    r = await client.get("/ui")
    assert 'window._akey=""' in r.text
    assert 'window._agent_role="owner"' in r.text


@pytest.mark.asyncio
async def test_session_cookie_grants_owner_api_access(client):
    await _login(client)
    r = await client.get("/agents", headers=UI_HDR)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_logout_revokes_a_captured_session_cookie(client):
    token = await _login(client)
    assert (await client.get("/agents", headers=UI_HDR)).status_code == 200

    await client.get("/ui/logout", follow_redirects=False)

    client.cookies.set("session", token)
    replay = await client.get("/agents", headers=UI_HDR)
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_session_header_without_cookie_rejected(client):
    r = await client.get("/agents", headers=UI_HDR)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_api_key_agents_unaffected(client):
    r = await client.get("/agents", headers=HEADERS)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ui_requires_login_when_demo_disabled(client):
    # demo_mode is off by default — unauthenticated /ui must redirect to login
    r = await client.get("/ui", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/login"


@pytest.mark.asyncio
async def test_ui_demo_mode_serves_readonly_viewer(client, monkeypatch):
    import artel.server.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "demo_mode", True)
    r = await client.get("/ui", follow_redirects=False)
    assert r.status_code == 200
    assert 'window._agent_role="viewer"' in r.text
