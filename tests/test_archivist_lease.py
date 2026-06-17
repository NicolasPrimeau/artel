import pytest

from tests.conftest import HEADERS

ARCH_AGENT = "archivist"
ARCH_KEY = "archkey"
ARCH_HEADERS = {"x-agent-id": ARCH_AGENT, "x-api-key": ARCH_KEY}


@pytest.fixture
async def arch_client(client):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    db.execute(
        "INSERT INTO agents (id, api_key, role) VALUES (?,?,?)",
        (ARCH_AGENT, ARCH_KEY, "archivist"),
    )
    db.commit()
    return client


async def _lease(c, instance_id, ttl=60):
    return await c.post(
        "/archivist/lease",
        json={"instance_id": instance_id, "ttl_seconds": ttl},
        headers=ARCH_HEADERS,
    )


async def test_lease_granted_on_first_acquire(arch_client):
    r = await _lease(arch_client, "a")
    assert r.status_code == 200
    d = r.json()
    assert d["granted"] is True
    assert d["holder"] == "a"
    assert d["expires_at"]


async def test_lease_renew_same_instance(arch_client):
    await _lease(arch_client, "a")
    r = await _lease(arch_client, "a")
    assert r.json()["granted"] is True


async def test_lease_denied_for_second_instance(arch_client):
    await _lease(arch_client, "a")
    r = await _lease(arch_client, "b")
    d = r.json()
    assert d["granted"] is False
    assert d["holder"] == "a"


async def test_lease_acquirable_after_expiry(arch_client):
    import artel.store.db as db_mod

    await _lease(arch_client, "a")
    db = db_mod.get_db()
    db.execute(
        "UPDATE kv SET value=json_set(value,'$.expires_at','2000-01-01T00:00:00.000Z') "
        "WHERE key='archivist_lease'"
    )
    db.commit()
    r = await _lease(arch_client, "b")
    d = r.json()
    assert d["granted"] is True
    assert d["holder"] == "b"


async def test_lease_requires_archivist_role(client):
    r = await client.post(
        "/archivist/lease",
        json={"instance_id": "a", "ttl_seconds": 60},
        headers=HEADERS,
    )
    assert r.status_code == 403
