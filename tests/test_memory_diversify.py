import pytest

from tests.conftest import HEADERS


@pytest.fixture
def content_embed(monkeypatch):
    def fake(text):
        t = text.lower()
        return [
            1.0 if "alpha" in t else 0.0,
            1.0 if "beta" in t else 0.0,
            1.0 if "shared" in t else 0.0,
        ] + [0.0] * 381

    import artel.server.routes.memory as mem

    monkeypatch.setattr(mem, "embed", fake)
    return fake


async def _write(client, content):
    r = await client.post("/memory", json={"content": content, "confidence": 1.0}, headers=HEADERS)
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_diversify_pulls_in_the_distinct_entry(client, content_embed):
    await _write(client, "shared topic alpha one")
    await _write(client, "shared topic alpha two")
    await _write(client, "shared topic beta three")

    r = await client.get(
        "/memory/search",
        params={"q": "shared alpha beta", "limit": 2, "diversify": "true"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()]
    assert len(contents) == 2
    assert any("beta" in c for c in contents)


@pytest.mark.asyncio
async def test_search_without_diversify_still_works(client, content_embed):
    await _write(client, "shared topic alpha one")
    await _write(client, "shared topic alpha two")
    await _write(client, "shared topic beta three")

    r = await client.get(
        "/memory/search",
        params={"q": "shared alpha beta", "limit": 2},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert len(r.json()) == 2
