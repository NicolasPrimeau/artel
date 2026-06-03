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


READ_ONLY_TOOLS = {
    "session_context",
    "memory_search",
    "memory_list",
    "memory_get",
    "memory_delta",
    "project_list",
    "project_members",
    "agent_list",
    "inbox_cron_setup",
    "message_inbox",
    "message_list",
    "task_list",
    "task_get",
    "feed_list",
}

DESTRUCTIVE_TOOLS = {"memory_delete", "agent_delete"}

IDEMPOTENT_TOOLS = {
    "session_handoff",
    "memory_update",
    "project_join",
    "project_leave",
    "message_mark_read",
    "task_update",
    "feed_unsubscribe",
    "agent_rename",
}

MUTATING_TOOLS = (
    {
        "memory_write",
        "message_send",
        "task_create",
        "task_claim",
        "task_unclaim",
        "task_complete",
        "task_fail",
        "task_comment",
        "event_emit",
        "feed_subscribe",
    }
    | DESTRUCTIVE_TOOLS
    | IDEMPOTENT_TOOLS
)


@pytest.mark.asyncio
async def test_tool_annotations_are_consistent(client):
    r = await client.get("/.well-known/mcp/server-card.json")
    by_name = {t["name"]: t["annotations"] for t in r.json()["tools"]}

    for name in READ_ONLY_TOOLS:
        assert by_name[name]["readOnlyHint"] is True, name

    for name in MUTATING_TOOLS:
        assert by_name[name]["readOnlyHint"] is not True, name

    for name in DESTRUCTIVE_TOOLS:
        assert by_name[name]["destructiveHint"] is True, name

    for name in IDEMPOTENT_TOOLS:
        assert by_name[name]["idempotentHint"] is True, name

    for name, ann in by_name.items():
        if name not in DESTRUCTIVE_TOOLS:
            assert ann["destructiveHint"] is not True, name
