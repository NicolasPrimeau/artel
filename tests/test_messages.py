from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


async def test_send_and_receive(client):
    r = await client.post(
        "/messages", json={"to": AGENT2, "subject": "hello", "body": "world"}, headers=HEADERS
    )
    assert r.status_code == 201
    msg = r.json()
    assert msg["from_agent"] == TEST_AGENT
    assert msg["to_agent"] == AGENT2
    assert msg["read"] is False


async def test_inbox_shows_unread(client):
    await client.post("/messages", json={"to": AGENT2, "body": "msg1"}, headers=HEADERS)
    await client.post("/messages", json={"to": AGENT2, "body": "msg2"}, headers=HEADERS)

    r = await client.get("/messages/inbox", headers=HEADERS2)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_inbox_excludes_other_agent(client):
    await client.post(
        "/messages", json={"to": TEST_AGENT, "body": "for testagent"}, headers=HEADERS2
    )

    r = await client.get("/messages/inbox", headers=HEADERS2)
    assert r.status_code == 200
    assert len(r.json()) == 0


async def test_mark_read(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "read me"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.post(f"/messages/{mid}/read", headers=HEADERS2)
    assert r2.status_code == 200
    assert r2.json()["read"] is True


async def test_inbox_empty_after_mark_read(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "ephemeral"}, headers=HEADERS)
    mid = r.json()["id"]

    await client.post(f"/messages/{mid}/read", headers=HEADERS2)

    r2 = await client.get("/messages/inbox", headers=HEADERS2)
    assert len(r2.json()) == 0


async def test_broadcast_message_received_by_all(client):
    r = await client.post(
        "/messages", json={"to": "broadcast", "body": "attention all"}, headers=HEADERS
    )
    assert r.status_code == 201

    r1 = await client.get("/messages/inbox", headers=HEADERS)
    r2 = await client.get("/messages/inbox", headers=HEADERS2)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(r1.json()) == 1
    assert len(r2.json()) == 1


async def test_mark_read_wrong_agent_forbidden(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "private"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.post(f"/messages/{mid}/read", headers=HEADERS)
    assert r2.status_code == 403


async def test_get_message_by_id(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "fetchable"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.get(f"/messages/{mid}", headers=HEADERS2)
    assert r2.status_code == 200
    assert r2.json()["body"] == "fetchable"


async def test_get_message_sender_can_fetch(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "sent"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.get(f"/messages/{mid}", headers=HEADERS)
    assert r2.status_code == 200


async def test_get_message_wrong_agent_forbidden(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "private"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.get(f"/messages/{mid}", headers={"x-agent-id": "other", "x-api-key": "nope"})
    assert r2.status_code == 401


async def test_message_event_written_to_db(client):
    await client.post("/messages", json={"to": AGENT2, "body": "event check"}, headers=HEADERS)

    r = await client.get("/events", params={"since": "1970-01-01T00:00:00.000Z"}, headers=HEADERS)
    events = r.json()
    types = [e["type"] for e in events]
    assert "message.received" in types


async def test_list_messages_shows_sent_and_received(client):
    await client.post("/messages", json={"to": AGENT2, "body": "sent"}, headers=HEADERS)
    await client.post("/messages", json={"to": TEST_AGENT, "body": "received"}, headers=HEADERS2)

    r = await client.get("/messages", headers=HEADERS)
    assert r.status_code == 200
    bodies = [m["body"] for m in r.json()]
    assert "sent" in bodies
    assert "received" in bodies


async def test_list_messages_read_filter(client):
    r = await client.post("/messages", json={"to": AGENT2, "body": "unread"}, headers=HEADERS)
    mid = r.json()["id"]

    r2 = await client.get("/messages", params={"read": "false"}, headers=HEADERS2)
    assert any(m["id"] == mid for m in r2.json())

    await client.post(f"/messages/{mid}/read", headers=HEADERS2)

    r3 = await client.get("/messages", params={"read": "false"}, headers=HEADERS2)
    assert not any(m["id"] == mid for m in r3.json())

    r4 = await client.get("/messages", params={"read": "true"}, headers=HEADERS2)
    assert any(m["id"] == mid for m in r4.json())


# ── Project inboxes ──────────────────────────────────────────────────────────


async def _join(client, project: str, headers: dict) -> None:
    r = await client.post(f"/projects/{project}/join", headers=headers)
    assert r.status_code == 204


async def test_project_inbox_member_can_send_and_receive(client):
    await _join(client, "alpha", HEADERS)
    await _join(client, "alpha", HEADERS2)

    r = await client.post(
        "/messages", json={"to": "project:alpha", "body": "team update"}, headers=HEADERS
    )
    assert r.status_code == 201

    inbox = await client.get("/messages/inbox", headers=HEADERS2)
    assert inbox.status_code == 200
    bodies = [m["body"] for m in inbox.json()]
    assert "team update" in bodies


async def test_project_inbox_non_member_cannot_send(client):
    await _join(client, "alpha", HEADERS2)

    r = await client.post(
        "/messages", json={"to": "project:alpha", "body": "intrusion"}, headers=HEADERS
    )
    assert r.status_code == 403


async def test_project_inbox_non_member_does_not_receive(client):
    await _join(client, "alpha", HEADERS)
    # HEADERS2 is not in alpha
    r = await client.post(
        "/messages", json={"to": "project:alpha", "body": "alpha-only"}, headers=HEADERS
    )
    assert r.status_code == 201

    inbox = await client.get("/messages/inbox", headers=HEADERS2)
    bodies = [m["body"] for m in inbox.json()]
    assert "alpha-only" not in bodies


async def test_project_inbox_per_recipient_read_tracking(client):
    await _join(client, "alpha", HEADERS)
    await _join(client, "alpha", HEADERS2)

    r = await client.post(
        "/messages", json={"to": "project:alpha", "body": "track me"}, headers=HEADERS
    )
    mid = r.json()["id"]

    # HEADERS2 reads it
    await client.post(f"/messages/{mid}/read", headers=HEADERS2)
    inbox2 = await client.get("/messages/inbox", headers=HEADERS2)
    assert not any(m["id"] == mid for m in inbox2.json())

    # HEADERS (sender, also a member) still sees it in their unread inbox
    inbox1 = await client.get("/messages/inbox", headers=HEADERS)
    assert any(m["id"] == mid for m in inbox1.json())


async def test_project_inbox_unknown_project_404(client):
    r = await client.post("/messages", json={"to": "project:ghost", "body": "?"}, headers=HEADERS)
    assert r.status_code == 404


async def test_project_inbox_get_message_non_member_forbidden(client):
    await _join(client, "alpha", HEADERS)
    r = await client.post(
        "/messages", json={"to": "project:alpha", "body": "secret"}, headers=HEADERS
    )
    mid = r.json()["id"]

    r2 = await client.get(f"/messages/{mid}", headers=HEADERS2)
    assert r2.status_code == 403
