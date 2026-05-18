import pytest


@pytest.mark.asyncio
async def test_server_card_shape(client):
    r = await client.get("/.well-known/mcp/server-card.json")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"serverInfo", "description", "tools", "resources", "prompts"}
    assert j["serverInfo"]["name"] == "artel"
    assert j["serverInfo"]["version"]
    assert len(j["tools"]) >= 30
    t = j["tools"][0]
    assert t["name"] and "inputSchema" in t and "description" in t
    assert "hosted-backend" in j["description"]


@pytest.mark.asyncio
async def test_server_card_unauthenticated(client):
    r = await client.get("/.well-known/mcp/server-card.json")
    assert r.status_code == 200
