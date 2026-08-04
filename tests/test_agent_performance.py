import pytest

from artel.archivist.synthesis import _performance_block, _proven_assignee
from artel.store import performance
from artel.store.db import get_db
from tests.conftest import AGENT2, HEADERS, HEADERS2, TEST_AGENT


async def _run(client, title, tags, outcome, headers=HEADERS2):
    r = await client.post("/tasks", json={"title": title, "tags": tags}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=headers)
    await client.post(f"/tasks/{tid}/{outcome}", json={"body": "x"}, headers=headers)
    return tid


async def test_completion_records_overall_and_per_tag(client):
    await _run(client, "ship it", ["infra", "docs"], "complete")
    rows = {(r["agent_id"], r["tag"]): r for r in performance.summary(get_db())}
    assert rows[(AGENT2, "")]["completed"] == 1
    assert rows[(AGENT2, "infra")]["completed"] == 1
    assert rows[(AGENT2, "docs")]["completed"] == 1


async def test_failure_is_recorded_separately(client):
    await _run(client, "break it", ["infra"], "fail")
    row = [r for r in performance.summary(get_db(), ["infra"])][0]
    assert row["failed"] == 1
    assert row["completed"] == 0
    assert row["success_rate"] == 0.0


async def test_success_rate_accumulates(client):
    for i in range(3):
        await _run(client, f"ok {i}", ["infra"], "complete")
    await _run(client, "bad", ["infra"], "fail")
    row = [r for r in performance.summary(get_db(), ["infra"])][0]
    assert row["attempts"] == 4
    assert row["completed"] == 3
    assert row["success_rate"] == 0.75


async def test_duplicate_tags_are_counted_once(client):
    await _run(client, "dupe", ["infra", "infra"], "complete")
    row = [r for r in performance.summary(get_db(), ["infra"])][0]
    assert row["attempts"] == 1


async def test_performance_endpoint_exposes_history(client):
    await _run(client, "ship it", ["infra"], "complete")
    r = await client.get("/agents/performance", params={"tag": "infra"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()[0]["agent_id"] == AGENT2
    assert r.json()[0]["tag"] == "infra"


async def test_best_for_needs_a_real_track_record(client):
    for i in range(3):
        await _run(client, f"ok {i}", ["infra"], "complete")
    assert performance.best_for(get_db(), ["infra"], {AGENT2}) is None

    for i in range(2):
        await _run(client, f"more {i}", ["infra"], "complete")
    picked = performance.best_for(get_db(), ["infra"], {AGENT2})
    assert picked is not None
    assert picked[0] == AGENT2
    assert "5/5 completed" in picked[1]


async def test_best_for_ignores_agents_that_are_not_eligible(client):
    for i in range(5):
        await _run(client, f"ok {i}", ["infra"], "complete")
    assert performance.best_for(get_db(), ["infra"], {TEST_AGENT}) is None


async def test_best_for_rejects_a_poor_success_rate(client):
    for i in range(2):
        await _run(client, f"ok {i}", ["flaky"], "complete")
    for i in range(4):
        await _run(client, f"bad {i}", ["flaky"], "fail")
    assert performance.best_for(get_db(), ["flaky"], {AGENT2}) is None


HISTORY = [
    {
        "agent_id": "a",
        "tag": "infra",
        "completed": 9,
        "failed": 1,
        "attempts": 10,
        "success_rate": 0.9,
    },
    {
        "agent_id": "b",
        "tag": "infra",
        "completed": 6,
        "failed": 0,
        "attempts": 6,
        "success_rate": 1.0,
    },
    {
        "agent_id": "c",
        "tag": "infra",
        "completed": 2,
        "failed": 0,
        "attempts": 2,
        "success_rate": 1.0,
    },
]


def test_proven_assignee_prefers_the_strongest_record():
    assert _proven_assignee(HISTORY, ["infra"], {"a", "b", "c"})[0] == "b"


def test_proven_assignee_ignores_thin_records():
    assert _proven_assignee(HISTORY, ["infra"], {"c"}) is None


def test_proven_assignee_ignores_unrelated_tags():
    assert _proven_assignee(HISTORY, ["docs"], {"a", "b"}) is None


def test_proven_assignee_ignores_inactive_agents():
    assert _proven_assignee(HISTORY, ["infra"], {"a"})[0] == "a"
    assert _proven_assignee(HISTORY, ["infra"], set()) is None


def test_performance_block_lists_only_eligible_agents():
    block = _performance_block(HISTORY, {"a"})
    assert "a [infra]" in block
    assert "b [infra]" not in block


@pytest.mark.parametrize("tag,label", [("", "overall"), ("infra", "infra")])
def test_performance_block_labels_the_overall_row(tag, label):
    block = _performance_block(
        [
            {
                "agent_id": "a",
                "tag": tag,
                "completed": 1,
                "failed": 0,
                "attempts": 1,
                "success_rate": 1.0,
            }
        ],
        {"a"},
    )
    assert f"[{label}]" in block
