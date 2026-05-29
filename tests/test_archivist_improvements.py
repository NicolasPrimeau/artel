import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artel.archivist.synthesis import (
    run_task_triage,
    run_utilization_prune,
    suggest_task_assignment,
)


def _ago(days=0, hours=0) -> str:
    dt = datetime.now(UTC) - timedelta(days=days, hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _make_client(**overrides):
    client = MagicMock()
    client.get_task = AsyncMock(return_value={})
    client.list_tasks = AsyncMock(return_value=[])
    client.list_agents = AsyncMock(return_value=[])
    client.list_entries = AsyncMock(return_value=[])
    client.search_memory = AsyncMock(return_value=[])
    client.patch_task = AsyncMock(return_value={})
    client.patch_memory = AsyncMock(return_value={})
    client.add_task_comment = AsyncMock(return_value={"id": "cmt-id"})
    client.close_task_as_duplicate = AsyncMock()
    client.log = AsyncMock()
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


def _make_task(
    task_id="task-abc",
    title="Do something useful",
    project="artel",
    assigned_to=None,
    status="open",
    tags=None,
    created_at=None,
):
    return {
        "id": task_id,
        "title": title,
        "description": "",
        "expected_outcome": "",
        "project": project,
        "assigned_to": assigned_to,
        "status": status,
        "tags": tags or [],
        "created_at": created_at or _ago(hours=1),
        "updated_at": created_at or _ago(hours=1),
    }


def _make_entry(
    entry_id="mem-abc",
    content="Some knowledge",
    confidence=1.0,
    tags=None,
    agent_id="agent-a",
    project=None,
    created_at=None,
    type="memory",
    origin=None,
):
    return {
        "id": entry_id,
        "type": type,
        "content": content,
        "confidence": confidence,
        "tags": tags or [],
        "agent_id": agent_id,
        "project": project,
        "created_at": created_at or _ago(hours=1),
        "updated_at": created_at or _ago(hours=1),
        "origin": origin,
    }


def _make_agent(agent_id="agent-a", last_seen_at=None):
    return {
        "id": agent_id,
        "last_seen_at": last_seen_at or _ago(hours=1),
        "role": "member",
    }


# ── run_task_triage: new tagging behaviour ────────────────────────────────────


class TestTriageSkipsAlreadyTriaged:
    async def test_skips_task_with_archivist_triaged_tag(self):
        task = _make_task(tags=["archivist-triaged"])
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[_make_entry()]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.add_task_comment.assert_not_called()
        client.patch_task.assert_not_called()

    async def test_triages_task_without_tag_and_marks_it(self):
        task = _make_task(tags=[])
        mem = _make_entry()
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[mem]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.patch_task.assert_called()
        all_calls = str(client.patch_task.call_args_list)
        assert "archivist-triaged" in all_calls

    async def test_triage_tag_added_even_when_no_memory_found(self):
        task = _make_task(tags=[])
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        # patch_task should still be called to mark as triaged
        all_calls = str(client.patch_task.call_args_list)
        assert "archivist-triaged" in all_calls

    async def test_does_not_log_when_nothing_happens(self):
        task = _make_task(tags=["archivist-triaged", "archivist-stale-flagged"])
        client = _make_client(list_tasks=AsyncMock(return_value=[task]))
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.log.assert_not_called()


class TestTriageStaleFlagging:
    async def test_flags_task_open_longer_than_7_days(self):
        task = _make_task(created_at=_ago(days=10), tags=[])
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        all_calls = str(client.patch_task.call_args_list)
        assert "archivist-stale-flagged" in all_calls
        client.add_task_comment.assert_called()
        comment_body = client.add_task_comment.call_args.args[1]
        assert "[archivist]" in comment_body
        assert "10" in comment_body or "days" in comment_body.lower()

    async def test_does_not_double_flag_already_stale_flagged(self):
        task = _make_task(created_at=_ago(days=10), tags=["archivist-stale-flagged"])
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        # No stale comment added again
        for call in client.add_task_comment.call_args_list:
            body = call.args[1] if call.args else ""
            assert "days" not in body.lower() or "archivist-stale" not in body

    async def test_does_not_flag_recent_task(self):
        task = _make_task(created_at=_ago(days=3), tags=[])
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        all_calls = str(client.patch_task.call_args_list)
        assert "archivist-stale-flagged" not in all_calls

    async def test_logs_stale_flagged_count(self):
        task = _make_task(created_at=_ago(days=10), tags=[])
        client = _make_client(
            list_tasks=AsyncMock(return_value=[task]),
            search_memory=AsyncMock(return_value=[]),
        )
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await run_task_triage(client)
        client.log.assert_called_once()
        log_msg = client.log.call_args.kwargs.get("message", "") or client.log.call_args[1].get(
            "message", ""
        )
        assert "stale" in log_msg


# ── suggest_task_assignment ───────────────────────────────────────────────────


class TestSuggestTaskAssignment:
    async def test_skips_already_assigned_task(self):
        task = _make_task(assigned_to="someone")
        client = _make_client(get_task=AsyncMock(return_value=task))
        with patch("artel.archivist.synthesis.is_configured", return_value=True):
            await suggest_task_assignment("task-abc", client)
        client.list_agents.assert_not_called()
        client.add_task_comment.assert_not_called()

    async def test_skips_when_llm_not_configured(self):
        task = _make_task()
        client = _make_client(get_task=AsyncMock(return_value=task))
        with patch("artel.archivist.synthesis.is_configured", return_value=False):
            await suggest_task_assignment("task-abc", client)
        client.list_agents.assert_not_called()

    async def test_skips_when_fewer_than_2_active_agents(self):
        task = _make_task()
        agents = [_make_agent("only-one", last_seen_at=_ago(hours=1))]
        client = _make_client(
            get_task=AsyncMock(return_value=task),
            list_agents=AsyncMock(return_value=agents),
            list_tasks=AsyncMock(return_value=[]),
        )
        mock_complete = AsyncMock()
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", mock_complete),
        ):
            await suggest_task_assignment("task-abc", client)
        mock_complete.assert_not_called()
        client.add_task_comment.assert_not_called()

    async def test_skips_agents_not_seen_in_48h(self):
        task = _make_task()
        agents = [
            _make_agent("active-agent", last_seen_at=_ago(hours=1)),
            _make_agent("stale-agent", last_seen_at=_ago(hours=50)),
        ]
        client = _make_client(
            get_task=AsyncMock(return_value=task),
            list_agents=AsyncMock(return_value=agents),
            list_tasks=AsyncMock(return_value=[]),
        )
        mock_complete = AsyncMock()
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", mock_complete),
        ):
            await suggest_task_assignment("task-abc", client)
        mock_complete.assert_not_called()

    async def test_posts_suggestion_comment(self):
        task = _make_task()
        agents = [
            _make_agent("agent-a", last_seen_at=_ago(hours=1)),
            _make_agent("agent-b", last_seen_at=_ago(hours=2)),
        ]
        client = _make_client(
            get_task=AsyncMock(return_value=task),
            list_agents=AsyncMock(return_value=agents),
            list_tasks=AsyncMock(return_value=[]),
        )
        llm_resp = json.dumps({"agent_id": "agent-a", "rationale": "Has recent artel experience"})
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_resp)),
        ):
            await suggest_task_assignment("task-abc", client)
        client.add_task_comment.assert_called_once()
        body = client.add_task_comment.call_args.args[1]
        assert "[archivist]" in body
        assert "agent-a" in body
        assert "artel experience" in body

    async def test_ignores_suggestion_for_unknown_agent(self):
        task = _make_task()
        agents = [
            _make_agent("agent-a", last_seen_at=_ago(hours=1)),
            _make_agent("agent-b", last_seen_at=_ago(hours=2)),
        ]
        client = _make_client(
            get_task=AsyncMock(return_value=task),
            list_agents=AsyncMock(return_value=agents),
            list_tasks=AsyncMock(return_value=[]),
        )
        llm_resp = json.dumps({"agent_id": "hallucinated-agent", "rationale": "Seems good"})
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_resp)),
        ):
            await suggest_task_assignment("task-abc", client)
        client.add_task_comment.assert_not_called()

    async def test_null_agent_id_posts_no_comment(self):
        task = _make_task()
        agents = [
            _make_agent("agent-a", last_seen_at=_ago(hours=1)),
            _make_agent("agent-b", last_seen_at=_ago(hours=2)),
        ]
        client = _make_client(
            get_task=AsyncMock(return_value=task),
            list_agents=AsyncMock(return_value=agents),
            list_tasks=AsyncMock(return_value=[]),
        )
        llm_resp = json.dumps({"agent_id": None, "rationale": "No clear fit"})
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch("artel.archivist.synthesis.complete", AsyncMock(return_value=llm_resp)),
        ):
            await suggest_task_assignment("task-abc", client)
        client.add_task_comment.assert_not_called()

    async def test_task_fetch_failure_is_swallowed(self):
        client = _make_client(get_task=AsyncMock(side_effect=Exception("network error")))
        with patch("artel.archivist.synthesis.is_configured", return_value=True):
            await suggest_task_assignment("task-abc", client)
        client.add_task_comment.assert_not_called()

    async def test_llm_failure_is_swallowed(self):
        task = _make_task()
        agents = [
            _make_agent("agent-a", last_seen_at=_ago(hours=1)),
            _make_agent("agent-b", last_seen_at=_ago(hours=2)),
        ]
        client = _make_client(
            get_task=AsyncMock(return_value=task),
            list_agents=AsyncMock(return_value=agents),
            list_tasks=AsyncMock(return_value=[]),
        )
        with (
            patch("artel.archivist.synthesis.is_configured", return_value=True),
            patch(
                "artel.archivist.synthesis.complete", AsyncMock(side_effect=Exception("LLM down"))
            ),
        ):
            await suggest_task_assignment("task-abc", client)
        client.add_task_comment.assert_not_called()


# ── run_utilization_prune ─────────────────────────────────────────────────────


class TestRunUtilizationPrune:
    async def test_does_nothing_when_no_entries(self):
        client = _make_client(list_entries=AsyncMock(return_value=[]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
        ):
            mock_db.return_value.execute.return_value.fetchall.return_value = []
            await run_utilization_prune(client)
        client.patch_memory.assert_not_called()
        client.log.assert_not_called()

    async def test_skips_entries_younger_than_30_days(self):
        entry = _make_entry(created_at=_ago(days=10))
        client = _make_client(list_entries=AsyncMock(return_value=[entry]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
        ):
            mock_db.return_value.execute.return_value.fetchall.return_value = [
                {"id": entry["id"], "read_count": 0}
            ]
            await run_utilization_prune(client)
        client.patch_memory.assert_not_called()

    async def test_skips_entries_with_reads(self):
        entry = _make_entry(created_at=_ago(days=40))
        client = _make_client(list_entries=AsyncMock(return_value=[entry]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
        ):
            mock_db.return_value.execute.return_value.fetchall.return_value = [
                {"id": entry["id"], "read_count": 3}
            ]
            await run_utilization_prune(client)
        client.patch_memory.assert_not_called()

    async def test_decays_unread_old_entries(self):
        entry = _make_entry(created_at=_ago(days=40), confidence=1.0)
        client = _make_client(list_entries=AsyncMock(return_value=[entry]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
            patch("artel.archivist.synthesis.settings") as mock_settings,
        ):
            mock_settings.decay_floor = 0.1
            mock_db.return_value.execute.return_value.fetchall.return_value = [
                {"id": entry["id"], "read_count": 0}
            ]
            await run_utilization_prune(client)
        client.patch_memory.assert_called_once()
        call_kwargs = client.patch_memory.call_args
        entry_id = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
        assert entry_id == entry["id"]
        new_conf = call_kwargs.kwargs.get("confidence") or call_kwargs[1].get("confidence")
        assert new_conf == pytest.approx(0.7, abs=0.01)

    async def test_caps_at_20_entries(self):
        entries = [_make_entry(entry_id=f"mem-{i}", created_at=_ago(days=40)) for i in range(30)]
        client = _make_client(list_entries=AsyncMock(return_value=entries))
        read_data = [{"id": e["id"], "read_count": 0} for e in entries]
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
            patch("artel.archivist.synthesis.settings") as mock_settings,
        ):
            mock_settings.decay_floor = 0.1
            mock_db.return_value.execute.return_value.fetchall.return_value = read_data
            await run_utilization_prune(client)
        assert client.patch_memory.call_count == 20

    async def test_logs_when_entries_adjusted(self):
        entry = _make_entry(created_at=_ago(days=40), confidence=1.0)
        client = _make_client(list_entries=AsyncMock(return_value=[entry]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
            patch("artel.archivist.synthesis.settings") as mock_settings,
        ):
            mock_settings.decay_floor = 0.1
            mock_db.return_value.execute.return_value.fetchall.return_value = [
                {"id": entry["id"], "read_count": 0}
            ]
            await run_utilization_prune(client)
        client.log.assert_called_once()
        log_action = client.log.call_args.kwargs.get("action") or client.log.call_args[1].get(
            "action"
        )
        assert log_action == "utilization_prune"

    async def test_skips_doc_type_entries(self):
        doc_entry = _make_entry(created_at=_ago(days=40), type="doc")
        client = _make_client(list_entries=AsyncMock(return_value=[doc_entry]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
        ):
            mock_db.return_value.execute.return_value.fetchall.return_value = []
            await run_utilization_prune(client)
        client.patch_memory.assert_not_called()

    async def test_skips_remote_origin_entries(self):
        entry = _make_entry(created_at=_ago(days=40), origin="other-instance")
        client = _make_client(list_entries=AsyncMock(return_value=[entry]))
        with (
            patch("artel.archivist.synthesis.get_db") as mock_db,
            patch("artel.archivist.synthesis.instance_id", return_value="inst-1"),
        ):
            mock_db.return_value.execute.return_value.fetchall.return_value = []
            await run_utilization_prune(client)
        client.patch_memory.assert_not_called()
