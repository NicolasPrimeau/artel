"""
Messaging history and task project transfer scenarios.

Covers: GET /messages (full history), task project field in PATCH /tasks/:id.
"""


async def test_full_message_history_across_agents(scenario):
    """Two agents exchange messages; each can see full history including sent."""
    alpha = await scenario.agent("alpha")
    beta = await scenario.agent("beta")

    await alpha.send_message("beta", "hello from alpha", subject="greeting")
    await beta.send_message("alpha", "hello back", subject="reply")

    alpha_history = await alpha.list_messages()
    beta_history = await beta.list_messages()

    alpha_bodies = [m["body"] for m in alpha_history]
    assert "hello from alpha" in alpha_bodies
    assert "hello back" in alpha_bodies

    beta_bodies = [m["body"] for m in beta_history]
    assert "hello from alpha" in beta_bodies
    assert "hello back" in beta_bodies


async def test_message_history_read_filter(scenario):
    """Unread filter shows only unread; read filter shows only read."""
    sender = await scenario.agent("sender")
    receiver = await scenario.agent("receiver")

    msg = await sender.send_message("receiver", "check me")
    mid = msg["id"]

    unread = await receiver.list_messages(read="false")
    assert any(m["id"] == mid for m in unread)

    read_before = await receiver.list_messages(read="true")
    assert not any(m["id"] == mid for m in read_before)

    await receiver.mark_message_read(mid)

    unread_after = await receiver.list_messages(read="false")
    assert not any(m["id"] == mid for m in unread_after)

    read_after = await receiver.list_messages(read="true")
    assert any(m["id"] == mid for m in read_after)


async def test_message_history_excludes_unrelated(scenario):
    """Agent C cannot see messages between A and B."""
    a = await scenario.agent("agent-a")
    await scenario.agent("agent-b")
    c = await scenario.agent("agent-c")

    await a.send_message("agent-b", "private between a and b")

    c_history = await c.list_messages()
    assert not any(m["body"] == "private between a and b" for m in c_history)


async def test_task_transfer_to_project(scenario):
    """Task created without project is invisible to project-scoped queries; transfer fixes it."""
    coordinator = await scenario.agent("coordinator")
    worker = await scenario.agent("worker")

    await worker.join_project("ops")

    # coordinator isn't in ops yet, so the task is created unscoped (global)
    task = await coordinator.create_task("Audit log retention policy")
    assert task["project"] is None

    scoped = await worker.list_tasks(project="ops")
    assert not any(t["id"] == task["id"] for t in scoped)

    await coordinator.join_project("ops")
    transferred = await coordinator.update_task(task["id"], project="ops")
    assert transferred["project"] == "ops"

    scoped_after = await worker.list_tasks(project="ops")
    assert any(t["id"] == task["id"] for t in scoped_after)


async def test_task_transfer_non_member_blocked(scenario):
    """Agent cannot transfer a task into a project it doesn't belong to."""
    owner = await scenario.agent("owner")
    outsider = await scenario.agent("outsider")

    await owner.join_project("private-proj")

    task = await owner.create_task("Restricted work")

    r = await outsider._http.patch(f"/tasks/{task['id']}", json={"project": "private-proj"})
    assert r.status_code == 403


async def test_orphaned_task_discovered_and_transferred(scenario):
    """
    Realistic scenario: steward creates a task intending it for a project but
    forgets to set the project field. A coordinator notices it's missing from
    project-scoped queries, then transfers it.
    """
    steward = await scenario.agent("steward")
    coordinator = await scenario.agent("coordinator")

    await coordinator.join_project("nimbus")

    # steward isn't in nimbus yet, so the task lands unscoped (the orphan)
    task = await steward.create_task(
        "Set up DMARC records",
        description="SPF missing amazonses.com, no DMARC on any domain",
    )

    await steward.send_message(
        "coordinator",
        f"Created task {task['id']} for nimbus — DMARC work",
        subject="DMARC task created",
    )

    msgs = await coordinator.list_messages()
    assert any("DMARC" in m["subject"] for m in msgs)

    nimbus_tasks = await coordinator.list_tasks(project="nimbus")
    assert not any(t["id"] == task["id"] for t in nimbus_tasks)

    all_tasks = await coordinator.list_tasks()
    assert any(t["id"] == task["id"] for t in all_tasks)

    await coordinator.send_message(
        "steward", f"Task {task['id']} missing project — please transfer to nimbus"
    )

    steward_msgs = await steward.list_messages(read="false")
    assert any("missing project" in m["body"] for m in steward_msgs)

    await steward.join_project("nimbus")
    fixed = await steward.update_task(task["id"], project="nimbus")
    assert fixed["project"] == "nimbus"

    nimbus_after = await coordinator.list_tasks(project="nimbus")
    assert any(t["id"] == task["id"] for t in nimbus_after)
