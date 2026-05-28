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
    ann = t["annotations"]
    assert set(ann) == {
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    }
    assert len(j["prompts"]) >= 3
    p = j["prompts"][0]
    assert p["name"] and p["description"] and "arguments" in p
    assert "hosted-backend" in j["description"]
    by_name = {x["name"]: x for x in j["tools"]}
    assert by_name["memory_delete"]["annotations"]["destructiveHint"] is True
    assert by_name["memory_search"]["annotations"]["readOnlyHint"] is True


@pytest.mark.asyncio
async def test_server_card_unauthenticated(client):
    r = await client.get("/.well-known/mcp/server-card.json")
    assert r.status_code == 200
