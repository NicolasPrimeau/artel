import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from tests.conftest import TEST_AGENT, TEST_KEY

REDIRECT_URI = "https://example.com/cb"


def _pkce_pair():
    verifier = "verifier-0123456789abcdefghijklmnopqrstuvwxyz-ABCDEFG"
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def _authorize(client, challenge, redirect_uri=REDIRECT_URI):
    return await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": TEST_AGENT,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )


def _code_from(resp):
    return parse_qs(urlparse(resp.headers["location"]).query)["code"][0]


async def test_token_endpoint_returns_jwt(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["expires_in"] > 0
    assert len(body["access_token"].split(".")) == 3


async def test_token_wrong_credentials(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": TEST_AGENT,
            "client_secret": "wrongkey",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_client"


async def test_token_unsupported_grant_type(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "password",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


async def test_token_authorization_code_without_code(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


async def test_oauth_server_metadata(client):
    r = await client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert "token_endpoint" in body
    assert body["token_endpoint"].endswith("/oauth/token")
    assert "client_credentials" in body["grant_types_supported"]


async def test_bearer_token_authenticates_mcp_request(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    token = r.json()["access_token"]
    r2 = await client.get(
        "/memory",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200


async def test_bearer_token_invalid_rejected(client):
    r = await client.get(
        "/memory",
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert r.status_code == 401


async def test_self_register_requires_key_when_configured(client):
    r = await client.post("/agents/self-register", json={"agent_id": "newagent"})
    assert r.status_code == 401


async def test_self_register_with_correct_key(client):
    r = await client.post(
        "/agents/self-register",
        json={"agent_id": "newagent"},
        headers={"x-registration-key": "regkey"},
    )
    assert r.status_code == 201
    assert r.json()["agent_id"] == "newagent"


async def test_oauth_register_denied_without_key_when_configured(client):
    r = await client.post("/oauth/register", json={"client_name": "evil-client"})
    assert r.status_code == 403
    assert r.json()["error"] == "access_denied"


async def test_oauth_register_with_key_issues_usable_client(client):
    r = await client.post(
        "/oauth/register",
        json={"client_name": "good-client"},
        headers={"x-registration-key": "regkey"},
    )
    assert r.status_code == 201
    body = r.json()
    tok = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": body["client_id"],
            "client_secret": body["client_secret"],
        },
    )
    assert tok.status_code == 200


async def test_authorization_code_pkce_happy_path(client):
    verifier, challenge = _pkce_pair()
    auth = await _authorize(client, challenge)
    assert auth.status_code == 302
    code = _code_from(auth)
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": TEST_AGENT,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert r.status_code == 200
    assert len(r.json()["access_token"].split(".")) == 3


async def test_authorize_rejects_missing_pkce(client):
    r = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": TEST_AGENT,
            "redirect_uri": REDIRECT_URI,
            "state": "xyz",
        },
    )
    assert r.status_code == 302
    qs = parse_qs(urlparse(r.headers["location"]).query)
    assert qs["error"] == ["invalid_request"]


async def test_authorization_code_wrong_verifier_rejected(client):
    _verifier, challenge = _pkce_pair()
    code = _code_from(await _authorize(client, challenge))
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": TEST_AGENT,
            "code": code,
            "code_verifier": "the-wrong-verifier-entirely",
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


async def test_authorization_code_redirect_uri_mismatch_rejected(client):
    verifier, challenge = _pkce_pair()
    code = _code_from(await _authorize(client, challenge))
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": TEST_AGENT,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://attacker.example/cb",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


async def test_authorization_code_is_single_use(client):
    verifier, challenge = _pkce_pair()
    code = _code_from(await _authorize(client, challenge))
    data = {
        "grant_type": "authorization_code",
        "client_id": TEST_AGENT,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }
    first = await client.post("/oauth/token", data=data)
    assert first.status_code == 200
    second = await client.post("/oauth/token", data=data)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"
