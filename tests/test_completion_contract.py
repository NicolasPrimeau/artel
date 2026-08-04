import json

from artel.server.contract import validate_contract, validate_payload
from tests.conftest import HEADERS, HEADERS2

SOURCES_CONTRACT = {
    "type": "object",
    "required": ["sources"],
    "properties": {
        "sources": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "url"],
                "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
            },
        }
    },
}

GOOD_OUTPUT = {"sources": [{"name": "ontario", "url": "https://example.test/on"}]}


def test_validate_contract_accepts_supported_subset():
    assert validate_contract(SOURCES_CONTRACT) == []


def test_validate_contract_rejects_unknown_keyword():
    problems = validate_contract({"type": "object", "patternProperties": {}})
    assert any("patternProperties" in p for p in problems)


def test_validate_contract_rejects_unknown_type():
    problems = validate_contract({"type": "tuple"})
    assert any("tuple" in p for p in problems)


def test_validate_contract_recurses_into_properties_and_items():
    problems = validate_contract(
        {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "decimal"}}}}
    )
    assert any("output[].n" in p for p in problems)


def test_validate_payload_accepts_matching_output():
    assert validate_payload(SOURCES_CONTRACT, GOOD_OUTPUT) == []


def test_validate_payload_reports_missing_required():
    problems = validate_payload(SOURCES_CONTRACT, {})
    assert problems == ["output.sources: required property missing"]


def test_validate_payload_reports_wrong_type():
    problems = validate_payload(SOURCES_CONTRACT, {"sources": "ontario"})
    assert any("expected array" in p for p in problems)


def test_validate_payload_enforces_min_items():
    problems = validate_payload(SOURCES_CONTRACT, {"sources": []})
    assert any("minItems" in p for p in problems)


def test_validate_payload_validates_each_item_by_index():
    problems = validate_payload(
        SOURCES_CONTRACT,
        {"sources": [{"name": "ok", "url": "u"}, {"name": "missing url"}]},
    )
    assert problems == ["output.sources[1].url: required property missing"]


def test_validate_payload_booleans_are_not_integers():
    assert validate_payload({"type": "integer"}, True) != []
    assert validate_payload({"type": "boolean"}, True) == []


def test_validate_payload_enum_and_min_length():
    assert validate_payload({"enum": ["a", "b"]}, "c") != []
    assert validate_payload({"type": "string", "minLength": 3}, "ab") != []
    assert validate_payload({"type": "string", "minLength": 3}, "abc") == []


async def _claimed_task(client, contract=None):
    body = {"title": "discover sources"}
    if contract is not None:
        body["completion_contract"] = contract
    r = await client.post("/tasks", json=body, headers=HEADERS)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS2)
    return tid


async def test_create_rejects_malformed_contract(client):
    r = await client.post(
        "/tasks",
        json={"title": "bad contract", "completion_contract": {"type": "widget"}},
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "completion_contract" in r.json()["detail"]


async def test_contract_round_trips_on_the_task(client):
    tid = await _claimed_task(client, SOURCES_CONTRACT)
    r = await client.get(f"/tasks/{tid}", headers=HEADERS)
    assert r.json()["completion_contract"] == SOURCES_CONTRACT
    assert r.json()["completion_payload"] is None


async def test_complete_rejects_missing_output(client):
    tid = await _claimed_task(client, SOURCES_CONTRACT)
    r = await client.post(f"/tasks/{tid}/complete", json={"body": "done"}, headers=HEADERS2)
    assert r.status_code == 422
    assert "requires an output" in json.dumps(r.json()["detail"])
    task = (await client.get(f"/tasks/{tid}", headers=HEADERS)).json()
    assert task["status"] == "claimed"


async def test_complete_rejects_malformed_output(client):
    tid = await _claimed_task(client, SOURCES_CONTRACT)
    r = await client.post(
        f"/tasks/{tid}/complete",
        json={"body": "done", "output": {"sources": [{"name": "no url"}]}},
        headers=HEADERS2,
    )
    assert r.status_code == 422
    task = (await client.get(f"/tasks/{tid}", headers=HEADERS)).json()
    assert task["status"] == "claimed"
    assert task["completion_payload"] is None


async def test_complete_accepts_and_stores_valid_output(client):
    tid = await _claimed_task(client, SOURCES_CONTRACT)
    r = await client.post(
        f"/tasks/{tid}/complete",
        json={"body": "done", "output": GOOD_OUTPUT},
        headers=HEADERS2,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert r.json()["completion_payload"] == GOOD_OUTPUT


async def test_completed_event_carries_the_output(client):
    tid = await _claimed_task(client, SOURCES_CONTRACT)
    await client.post(
        f"/tasks/{tid}/complete",
        json={"body": "done", "output": GOOD_OUTPUT},
        headers=HEADERS2,
    )
    from artel.store.db import get_db

    row = (
        get_db()
        .execute(
            "SELECT payload FROM events WHERE type='task.completed' ORDER BY created_at DESC LIMIT 1"
        )
        .fetchone()
    )
    assert json.loads(row["payload"])["output"] == GOOD_OUTPUT


async def test_tasks_without_a_contract_are_unchanged(client):
    tid = await _claimed_task(client)
    r = await client.post(f"/tasks/{tid}/complete", json={"body": "done"}, headers=HEADERS2)
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert r.json()["completion_contract"] is None
    assert r.json()["completion_payload"] is None


async def test_output_without_a_contract_is_stored_as_is(client):
    tid = await _claimed_task(client)
    r = await client.post(
        f"/tasks/{tid}/complete",
        json={"body": "done", "output": {"anything": [1, 2]}},
        headers=HEADERS2,
    )
    assert r.status_code == 200
    assert r.json()["completion_payload"] == {"anything": [1, 2]}
