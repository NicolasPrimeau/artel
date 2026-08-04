import asyncio

import httpx
import pytest_asyncio
from httpx import ASGITransport

from tests.conftest import AGENT2, KEY2, TEST_AGENT, TEST_KEY


@pytest_asyncio.fixture
async def mcp(tmp_path, monkeypatch):
    import artel.mcp.server as mcp_mod
    import artel.server.broadcast as bc_mod
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    db_mod._conn = None
    bc_mod._subscribers.clear()

    test_db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(cfg_mod.settings, "db_path", test_db_path)
    monkeypatch.setattr(cfg_mod.settings, "registration_key", "regkey")
    monkeypatch.setattr(mcp_mod.settings, "mcp_agent_id", TEST_AGENT)

    conn = db_mod.get_db(test_db_path)
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (TEST_AGENT, TEST_KEY))
    conn.execute("INSERT INTO agents (id, api_key) VALUES (?, ?)", (AGENT2, KEY2))
    conn.commit()

    from artel.server.app import app

    def test_http():
        return httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"x-agent-id": TEST_AGENT, "x-api-key": TEST_KEY},
            timeout=30.0,
        )

    monkeypatch.setattr(mcp_mod, "_http", test_http)

    yield mcp_mod

    if db_mod._conn:
        db_mod._conn.close()
        db_mod._conn = None
    bc_mod._subscribers.clear()


def _extract_id(result: str) -> str:
    return result.split("[")[1].split("]")[0]


async def test_memory_write_returns_id(mcp):
    result = await mcp.memory_write("the sky is blue")
    assert result.startswith("written [")


async def test_memory_write_private_scope(mcp):
    result = await mcp.memory_write("secret thought", scope="agent")
    assert result.startswith("written [")


async def test_memory_get_full_content(mcp):
    write = await mcp.memory_write("full content here")
    entry_id = _extract_id(write)
    result = await mcp.memory_get(entry_id)
    assert "full content here" in result
    assert entry_id in result
    assert "conf=" in result


async def test_memory_get_not_found(mcp):
    result = await mcp.memory_get("00000000-0000-0000-0000-000000000000")
    assert result.startswith("error 404")


async def test_memory_search_returns_results(mcp):
    await mcp.memory_write("python is a programming language")
    result = await mcp.memory_search("programming")
    assert result != "No results."
    assert "python is a programming language" in result


async def test_memory_search_no_results(mcp):
    result = await mcp.memory_search("xyzzy nonexistent query 12345")
    assert result == "No results."


async def test_memory_delta(mcp):
    await mcp.memory_write("delta entry")
    result = await mcp.memory_delta("1970-01-01T00:00:00.000Z")
    assert result != "No changes."
    assert "delta entry" in result


async def test_memory_delta_empty(mcp):
    result = await mcp.memory_delta("2099-01-01T00:00:00.000Z")
    assert result == "No changes."


async def test_task_get_by_id(mcp):
    create = await mcp.task_create("detailed task", description="lots of detail here")
    task_id = _extract_id(create)
    result = await mcp.task_get(task_id)
    assert task_id in result
    assert "detailed task" in result
    assert "lots of detail here" in result
    assert "created by:" in result


async def test_task_get_not_found(mcp):
    result = await mcp.task_get("00000000-0000-0000-0000-000000000000")
    assert result.startswith("error 404")


async def test_task_create_returns_summary(mcp):
    result = await mcp.task_create("fix the login bug", priority="high")
    assert result.startswith("created [")
    assert "high" in result
    assert "fix the login bug" in result


async def test_task_claim_returns_title(mcp):
    create = await mcp.task_create("claimable task")
    task_id = _extract_id(create)
    result = await mcp.task_claim(task_id)
    assert result.startswith("claimed [")
    assert "claimable task" in result


async def test_task_complete_returns_title(mcp):
    create = await mcp.task_create("completable task")
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_complete(task_id)
    assert result.startswith("completed [")
    assert "completable task" in result


async def test_task_fail_returns_title(mcp):
    create = await mcp.task_create("failable task")
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_fail(task_id)
    assert result.startswith("failed [")
    assert "failable task" in result


async def test_task_list_project_filter(mcp):
    await mcp.task_create("global task")  # no membership yet -> stays global
    await mcp.project_join("proj-a")
    await mcp.task_create("in proj-a", project="proj-a")
    result = await mcp.task_list(project="proj-a")
    assert "in proj-a" in result
    assert "global task" not in result


async def test_task_list_status_filter(mcp):
    await mcp.task_create("open task")
    result = await mcp.task_list(status="open")
    assert "open task" in result
    result_completed = await mcp.task_list(status="completed")
    assert "open task" not in result_completed


async def test_task_list_empty(mcp):
    result = await mcp.task_list(status="completed")
    assert result == "No tasks."


async def test_task_claim_not_found(mcp):
    result = await mcp.task_claim("00000000-0000-0000-0000-000000000000")
    assert result.startswith("error 404")


async def test_task_claim_already_claimed(mcp):
    create = await mcp.task_create("double-claim")
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_claim(task_id)
    assert result.startswith("error 409")


async def test_send_message_returns_confirmation(mcp):
    result = await mcp.message_send(to=AGENT2, body="hello", subject="greet")
    assert result.startswith("sent to")
    assert AGENT2 in result


async def test_read_inbox_empty(mcp):
    result = await mcp.message_inbox()
    assert result == "No unread messages."


async def test_list_participants(mcp):
    result = await mcp.agent_list()
    assert TEST_AGENT in result
    assert AGENT2 in result


async def test_session_context_no_args_uses_own_id(mcp):
    result = await mcp.session_context()
    assert "No previous session" in result
    assert "error" not in result


async def test_session_context_explicit_agent_id(mcp):
    result = await mcp.session_context(agent_id=TEST_AGENT)
    assert "No previous session" in result


async def test_session_handoff_and_context(mcp):
    handoff = await mcp.session_handoff(
        summary="finished the auth refactor",
        next_steps=["deploy to prod", "monitor errors"],
        in_progress=["task-123"],
    )
    assert handoff.startswith("handoff saved [")

    context = await mcp.session_context()
    assert "finished the auth refactor" in context
    assert "deploy to prod" in context
    assert "monitor errors" in context


async def test_session_context_includes_memory_delta(mcp):
    await mcp.session_handoff(summary="first session")
    await asyncio.sleep(0.005)
    await mcp.memory_write("something new after handoff")

    context = await mcp.session_context()
    assert "something new after handoff" in context


async def test_agent_delete_self(mcp):
    import artel.store.db as db_mod

    result = await mcp.agent_delete()
    assert "deregistered" in result
    assert "credentials" in result
    row = db_mod.get_db().execute("SELECT id FROM agents WHERE id=?", (TEST_AGENT,)).fetchone()
    assert row is None


async def test_agent_delete_removes_from_participants(mcp, monkeypatch):
    import artel.mcp.server as mcp_mod
    from artel.server.app import app

    await mcp.agent_delete()

    def agent2_http():
        return httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"x-agent-id": AGENT2, "x-api-key": KEY2},
            timeout=30.0,
        )

    monkeypatch.setattr(mcp_mod, "_http", agent2_http)
    result = await mcp.agent_list()
    assert TEST_AGENT not in result


# ── Notification queue persistence ───────────────────────────────────────────


async def test_notification_queued_when_no_session(mcp):
    import artel.store.db as db_mod

    mcp._sessions.clear()
    mcp._enqueue_notification(TEST_AGENT, "inbox: new message from otheragent")

    row = (
        db_mod.get_db()
        .execute(
            "SELECT message, delivered_at FROM mcp_notification_queue WHERE agent_id=?",
            (TEST_AGENT,),
        )
        .fetchone()
    )
    assert row is not None
    assert row["message"] == "inbox: new message from otheragent"
    assert row["delivered_at"] is None


async def test_flush_notifications_delivers_queued(mcp):
    from unittest.mock import AsyncMock

    import artel.store.db as db_mod

    mcp._enqueue_notification(TEST_AGENT, "queued notification 1")
    mcp._enqueue_notification(TEST_AGENT, "queued notification 2")

    mock_session = AsyncMock()
    await mcp._flush_notifications(TEST_AGENT, mock_session)

    assert mock_session.send_log_message.call_count == 2
    undelivered = (
        db_mod.get_db()
        .execute(
            "SELECT id FROM mcp_notification_queue WHERE agent_id=? AND delivered_at IS NULL",
            (TEST_AGENT,),
        )
        .fetchall()
    )
    assert len(undelivered) == 0


async def test_flush_notifications_empty(mcp):
    from unittest.mock import AsyncMock

    mock_session = AsyncMock()
    await mcp._flush_notifications(TEST_AGENT, mock_session)
    mock_session.send_log_message.assert_not_called()


async def test_deliver_notification_queues_when_no_session(mcp):
    import artel.store.db as db_mod

    mcp._sessions.clear()
    await mcp._deliver_notification(TEST_AGENT, "inbox: new message from otheragent")

    row = (
        db_mod.get_db()
        .execute("SELECT message FROM mcp_notification_queue WHERE agent_id=?", (TEST_AGENT,))
        .fetchone()
    )
    assert row is not None
    assert row["message"] == "inbox: new message from otheragent"


async def test_deliver_notification_sends_live_when_session_present(mcp):
    from unittest.mock import AsyncMock

    import artel.store.db as db_mod

    mock_session = AsyncMock()
    mcp._sessions[TEST_AGENT] = mock_session
    try:
        await mcp._deliver_notification(TEST_AGENT, "live notification")
        mock_session.send_log_message.assert_called_once_with("warning", "live notification")
        row = (
            db_mod.get_db()
            .execute("SELECT id FROM mcp_notification_queue WHERE agent_id=?", (TEST_AGENT,))
            .fetchone()
        )
        assert row is None
    finally:
        mcp._sessions.pop(TEST_AGENT, None)


async def test_notification_queue_restart_survival(mcp):
    """Simulate restart: notifications queued while sessions absent, flushed on reconnect."""
    from unittest.mock import AsyncMock

    import artel.store.db as db_mod

    mcp._sessions.clear()

    await mcp._deliver_notification(TEST_AGENT, "inbox: new message from otheragent")

    mock_session = AsyncMock()
    mcp._sessions[TEST_AGENT] = mock_session
    try:
        await mcp._flush_notifications(TEST_AGENT, mock_session)

        mock_session.send_log_message.assert_called_once_with(
            "warning", "inbox: new message from otheragent"
        )
        row = (
            db_mod.get_db()
            .execute(
                "SELECT delivered_at FROM mcp_notification_queue WHERE agent_id=?", (TEST_AGENT,)
            )
            .fetchone()
        )
        assert row is not None
        assert row["delivered_at"] is not None
    finally:
        mcp._sessions.pop(TEST_AGENT, None)


async def test_memory_output_includes_version(mcp):
    write = await mcp.memory_write("versioned entry")
    entry_id = _extract_id(write)
    result = await mcp.memory_get(entry_id)
    assert " v1 " in result


async def test_memory_update_optimistic_lock_success(mcp):
    write = await mcp.memory_write("lock me")
    entry_id = _extract_id(write)
    result = await mcp.memory_update(entry_id, confidence=0.5, expected_version=1)
    assert "conflict" not in result.lower()
    assert " v2 " in result


async def test_memory_update_optimistic_lock_conflict(mcp):
    write = await mcp.memory_write("contended entry")
    entry_id = _extract_id(write)
    # First update moves the entry to version 2
    await mcp.memory_update(entry_id, confidence=0.7)
    # A stale caller still thinks it is at version 1
    result = await mcp.memory_update(entry_id, confidence=0.2, expected_version=1)
    assert result.lower().startswith("conflict")
    # The conflicting write did not take effect
    current = await mcp.memory_get(entry_id)
    assert "conf=0.70" in current


async def test_memory_update_without_version_is_last_write_wins(mcp):
    write = await mcp.memory_write("lww entry")
    entry_id = _extract_id(write)
    await mcp.memory_update(entry_id, confidence=0.7)
    result = await mcp.memory_update(entry_id, confidence=0.2)
    assert "conflict" not in result.lower()


_COMPILE_SRC = "def g(x):\n    return x + 1\n\n\ndef f(y):\n    return g(y) * 2\n"


def _compile_payload(commit="c1"):
    from artel.compile import compile_source

    units = compile_source("pkg/m.py", _COMPILE_SRC)
    return {
        "commit": commit,
        "units": [
            {
                "path": u.path,
                "symbol": u.symbol,
                "lang": u.lang,
                "kind": u.kind,
                "start_line": u.start_line,
                "end_line": u.end_line,
                "sha": u.sha,
                "description": u.description,
                "deps": [{"kind": d.kind, "name": d.name} for d in u.deps],
            }
            for u in units
        ],
    }


async def test_compile_status_and_stale_via_mcp(mcp):
    c = mcp._http()
    r = await c.post("/compile", json=_compile_payload())
    assert r.status_code == 201, r.text

    status = await mcp.compile_status()
    assert "compiled node" in status
    assert "stale" in status

    stale = await mcp.compile_stale()
    assert "No stale" in stale


async def test_graph_link_and_neighbors_via_mcp(mcp):
    c = mcp._http()
    await c.post("/compile", json=_compile_payload())
    rows = (await c.get("/memory", params={"type": "compiled"})).json()
    a, b = rows[0]["id"], rows[1]["id"]

    link = await mcp.graph_link(a, b, "corroborates")
    assert "linked" in link

    nb = await mcp.graph_neighbors(a)
    assert "viability" in nb
    assert "corroborates" in nb


async def test_compile_setup_returns_install_command(mcp):
    result = mcp.compile_setup()
    assert "/compile/install.sh" in result
    assert "ARTEL_AGENT_ID" in result
    assert "artel_compile.py" in result


async def test_session_context_includes_knowledge_map(mcp):
    await mcp.memory_write(
        "A stable reference document about mesh", entry_type="doc", tags=["mesh"]
    )
    await mcp.memory_write("Another note about compile mode", entry_type="doc", tags=["compile"])
    result = await mcp.session_context()
    assert "## Knowledge map" in result
    assert "A stable reference document about mesh" in result
    assert "docs:" in result


async def test_session_context_map_prefers_headline(mcp):
    import artel.store.db as db_mod

    write = await mcp.memory_write("raw first line of the body", entry_type="doc")
    entry_id = _extract_id(write)
    db = db_mod.get_db()
    db.execute(
        "UPDATE memory SET headline=?, headline_version=version WHERE id=?",
        ("curated one-line summary", entry_id),
    )
    db.commit()
    result = await mcp.session_context()
    map_section = result.split("## Knowledge map", 1)[1].split("\n\n", 1)[0]
    assert "curated one-line summary" in map_section
    assert "raw first line of the body" not in map_section


async def test_credential_middleware_reads_project_header():
    import artel.mcp.server as mcp_mod
    from artel.mcp.config import request_project

    captured = {}

    async def app(scope, receive, send):
        captured["project"] = request_project.get()
        captured["agent"] = mcp_mod._agent_id.get(None)

    mw = mcp_mod._CredentialMiddleware(app)
    scope = {
        "type": "http",
        "headers": [
            (b"x-agent-id", b"poseidon"),
            (b"x-api-key", b"k"),
            (b"x-mcp-project", b"nimbus"),
        ],
    }

    async def recv():
        return {}

    async def snd(_m):
        pass

    await mw(scope, recv, snd)
    assert captured["project"] == "nimbus"
    assert captured["agent"] == "poseidon"


async def test_credential_middleware_absent_project_header_is_none():
    import artel.mcp.server as mcp_mod
    from artel.mcp.config import request_project

    captured = {}

    async def app(scope, receive, send):
        captured["project"] = request_project.get()

    mw = mcp_mod._CredentialMiddleware(app)
    scope = {"type": "http", "headers": [(b"x-agent-id", b"poseidon")]}

    async def recv():
        return {}

    async def snd(_m):
        pass

    await mw(scope, recv, snd)
    assert captured["project"] is None


def test_resolve_project_precedence():
    from artel.mcp.config import MCPSettings, request_project

    s = MCPSettings()
    s.mcp_project = "envproj"
    assert s.resolve_project("Override") == "override"
    assert s.resolve_project() == "envproj"

    tok = request_project.set("CtxProj")
    try:
        assert s.resolve_project() == "ctxproj"
        assert s.resolve_project("override") == "override"
    finally:
        request_project.reset(tok)
    assert s.resolve_project() == "envproj"


async def test_memory_write_defaults_project_from_request_context(mcp, monkeypatch):
    from artel.mcp.config import request_project, settings
    from artel.store.db import get_db

    monkeypatch.setattr(settings, "mcp_project", "")
    await mcp.project_join("nimbus")
    tok = request_project.set("nimbus")
    try:
        result = await mcp.memory_write("scoped by x-mcp-project header")
    finally:
        request_project.reset(tok)

    entry_id = _extract_id(result)
    row = get_db().execute("SELECT project FROM memory WHERE id=?", (entry_id,)).fetchone()
    assert row["project"] == "nimbus"


async def test_explicit_project_overrides_request_context(mcp, monkeypatch):
    from artel.mcp.config import request_project, settings
    from artel.store.db import get_db

    monkeypatch.setattr(settings, "mcp_project", "")
    await mcp.project_join("artel")
    tok = request_project.set("nimbus")
    try:
        result = await mcp.memory_write("explicit arg wins", project="artel")
    finally:
        request_project.reset(tok)

    entry_id = _extract_id(result)
    row = get_db().execute("SELECT project FROM memory WHERE id=?", (entry_id,)).fetchone()
    assert row["project"] == "artel"


async def test_mcp_auth_middleware_reads_project_header():
    from artel.mcp.config import request_project
    from artel.server.app import MCPAuthMiddleware

    seen = {}

    async def inner(scope, receive, send):
        seen["project"] = request_project.get()

    mw = MCPAuthMiddleware(inner)
    scope = {
        "type": "http",
        "headers": [
            (b"x-agent-id", b"poseidon"),
            (b"x-api-key", b"k"),
            (b"x-mcp-project", b"nimbus"),
        ],
        "query_string": b"",
    }

    async def recv():
        return {}

    async def snd(_m):
        pass

    await mw(scope, recv, snd)
    assert seen["project"] == "nimbus"
    assert request_project.get() is None  # reset after the request


async def test_mcp_auth_middleware_absent_project_header_is_none():
    from artel.mcp.config import request_project
    from artel.server.app import MCPAuthMiddleware

    seen = {}

    async def inner(scope, receive, send):
        seen["project"] = request_project.get()

    mw = MCPAuthMiddleware(inner)
    scope = {
        "type": "http",
        "headers": [(b"x-agent-id", b"poseidon"), (b"x-api-key", b"k")],
        "query_string": b"",
    }

    async def recv():
        return {}

    async def snd(_m):
        pass

    await mw(scope, recv, snd)
    assert seen["project"] is None


async def test_task_get_surfaces_the_completion_contract(mcp):
    contract = {"type": "object", "required": ["sources"]}
    create = await mcp.task_create("fan-out task", completion_contract=contract)
    task_id = _extract_id(create)
    result = await mcp.task_get(task_id)
    assert "completion contract" in result
    assert "sources" in result


async def test_task_complete_rejects_output_that_breaks_the_contract(mcp):
    create = await mcp.task_create(
        "contracted task",
        completion_contract={"type": "object", "required": ["sources"]},
    )
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_complete(task_id, body="done")
    assert result.startswith("error 422")


async def test_task_complete_accepts_contracted_output(mcp):
    create = await mcp.task_create(
        "contracted task",
        completion_contract={"type": "object", "required": ["sources"]},
    )
    task_id = _extract_id(create)
    await mcp.task_claim(task_id)
    result = await mcp.task_complete(task_id, body="done", output={"sources": ["a"]})
    assert result.startswith("completed [")
    assert "output" in await mcp.task_get(task_id)


async def _seed_blueprint(client_headers=None):
    from artel.server.blueprint import BlueprintCreate
    from artel.server.routes.blueprints import create_blueprint

    doc = {
        "name": "probe-mold",
        "description": "discover then probe",
        "params": ["domain"],
        "nodes": [
            {
                "id": "discover",
                "title": "Discover sources for {domain}",
                "completion_contract": {"type": "object", "required": ["sources"]},
            },
            {
                "id": "probe",
                "title": "Probe {item}",
                "deps": ["discover"],
                "foreach": "discover.sources",
            },
        ],
    }
    return await create_blueprint(BlueprintCreate(document=doc), TEST_AGENT)


async def test_blueprint_list_and_instantiate(mcp):
    await _seed_blueprint()
    listed = await mcp.blueprint_list()
    assert "probe-mold" in listed
    assert "params: domain" in listed

    run = await mcp.blueprint_instantiate("probe-mold", {"domain": "liquor"})
    assert "[running]" in run
    assert "Discover sources for liquor" in run


async def test_blueprint_instantiate_rejects_missing_params(mcp):
    await _seed_blueprint()
    result = await mcp.blueprint_instantiate("probe-mold", {})
    assert result.startswith("error 422")


async def test_blueprint_run_shows_the_fan_out(mcp):
    await _seed_blueprint()
    run = await mcp.blueprint_instantiate("probe-mold", {"domain": "liquor"})
    run_id = run.split("run [")[1].split("]")[0]
    task_id = run.split("task: ")[1].strip().splitlines()[0]

    await mcp.task_claim(task_id)
    await mcp.task_complete(task_id, output={"sources": ["ontario", "quebec"]})

    after = await mcp.blueprint_run(run_id)
    assert "Probe ontario" in after
    assert "Probe quebec" in after
