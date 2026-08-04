import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artel.archivist import blueprint_compile
from artel.archivist.blueprint_compile import run_blueprint_compilation
from artel.server.blueprint import BlueprintDocument, validate_document

MOLD_SKILL = {
    "id": "skill-1",
    "version": 3,
    "content": "For each jurisdiction discovered, write one config, then ship.",
}

GOOD_DOC = {
    "name": "new-product",
    "description": "discover then configure",
    "params": ["domain"],
    "nodes": [
        {
            "id": "discover",
            "title": "Discover jurisdictions for {domain}",
            "completion_contract": {"type": "object", "required": ["jurisdictions"]},
        },
        {
            "id": "configure",
            "title": "Write the config for {item}",
            "deps": ["discover"],
            "foreach": "discover.jurisdictions",
        },
    ],
}


def _client(skills, blueprints=None, create=None):
    c = MagicMock()
    c.list_entries = AsyncMock(return_value=skills)
    c.list_blueprints = AsyncMock(return_value=blueprints or [])
    c.create_blueprint = create or AsyncMock(
        return_value={"id": "bp-1", "name": "new-product", "version": 1}
    )
    c.log = AsyncMock()
    return c


def _rejection(problems):
    request = httpx.Request("POST", "http://test/blueprints")
    response = httpx.Response(422, json={"detail": {"blueprint": problems}}, request=request)
    return httpx.HTTPStatusError("422", request=request, response=response)


@pytest.mark.asyncio
async def test_compiles_a_tagged_skill_and_stamps_its_source(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    c = _client([MOLD_SKILL])
    compiler = AsyncMock(return_value=GOOD_DOC)

    assert await run_blueprint_compilation(c, compiler=compiler) == 1
    c.list_entries.assert_awaited_once()
    assert c.list_entries.call_args.kwargs["type"] == "skill"
    assert c.list_entries.call_args.kwargs["tag"] == "blueprint"
    kwargs = c.create_blueprint.call_args.kwargs
    assert kwargs["source_entry_id"] == "skill-1"
    assert kwargs["source_version"] == 3


@pytest.mark.asyncio
async def test_skips_a_skill_already_compiled_at_this_version(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    c = _client(
        [MOLD_SKILL],
        blueprints=[{"source_entry_id": "skill-1", "source_version": 3}],
    )
    compiler = AsyncMock(return_value=GOOD_DOC)

    assert await run_blueprint_compilation(c, compiler=compiler) == 0
    compiler.assert_not_awaited()
    c.create_blueprint.assert_not_awaited()


@pytest.mark.asyncio
async def test_recompiles_when_the_skill_was_edited(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    c = _client(
        [MOLD_SKILL],
        blueprints=[{"source_entry_id": "skill-1", "source_version": 2}],
    )
    compiler = AsyncMock(return_value=GOOD_DOC)

    assert await run_blueprint_compilation(c, compiler=compiler) == 1
    compiler.assert_awaited_once()


@pytest.mark.asyncio
async def test_validator_rejection_is_fed_back_for_one_repair_attempt(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    create = AsyncMock(
        side_effect=[
            _rejection(["configure: foreach over 'discover' requires it as a dependency"]),
            {"id": "bp-1", "name": "new-product", "version": 1},
        ]
    )
    c = _client([MOLD_SKILL], create=create)
    broken = json.loads(json.dumps(GOOD_DOC))
    broken["nodes"][1]["deps"] = []
    compiler = AsyncMock(side_effect=[broken, GOOD_DOC])

    assert await run_blueprint_compilation(c, compiler=compiler) == 1
    assert compiler.await_count == 2
    problems = compiler.await_args_list[1].args[1]
    assert "requires it as a dependency" in problems[0]


@pytest.mark.asyncio
async def test_gives_up_after_the_repair_attempt_also_fails(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    create = AsyncMock(side_effect=_rejection(["cyclic: dependency cycle"]))
    c = _client([MOLD_SKILL], create=create)
    compiler = AsyncMock(return_value=GOOD_DOC)

    assert await run_blueprint_compilation(c, compiler=compiler) == 0
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_unparseable_model_output_does_not_crash_the_pass(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    c = _client([MOLD_SKILL])
    compiler = AsyncMock(side_effect=ValueError("not json"))

    assert await run_blueprint_compilation(c, compiler=compiler) == 0
    c.create_blueprint.assert_not_awaited()


@pytest.mark.asyncio
async def test_passive_mode_compiles_nothing(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: False)
    c = _client([MOLD_SKILL])

    assert await run_blueprint_compilation(c) == 0
    c.list_entries.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_tagged_skills_is_a_no_op(monkeypatch):
    monkeypatch.setattr(blueprint_compile, "is_configured", lambda: True)
    c = _client([])

    assert await run_blueprint_compilation(c, compiler=AsyncMock()) == 0
    c.list_blueprints.assert_not_awaited()


def test_the_prompt_contract_matches_the_validator():
    doc = BlueprintDocument(**GOOD_DOC)
    assert validate_document(doc) == []


@pytest.mark.asyncio
async def test_strip_fences_handles_a_fenced_model_reply(monkeypatch):
    captured = {}

    async def fake_complete(system, user, max_tokens):
        captured["user"] = user
        return "```json\n" + json.dumps(GOOD_DOC) + "\n```"

    monkeypatch.setattr(blueprint_compile, "complete", fake_complete)
    doc = await blueprint_compile._compile_with_llm("some skill prose", [])
    assert doc["name"] == "new-product"
    assert "some skill prose" in captured["user"]


@pytest.mark.asyncio
async def test_repair_prompt_carries_the_problems(monkeypatch):
    captured = {}

    async def fake_complete(system, user, max_tokens):
        captured["user"] = user
        return json.dumps(GOOD_DOC)

    monkeypatch.setattr(blueprint_compile, "complete", fake_complete)
    await blueprint_compile._compile_with_llm("prose", ["discover: unknown dependency 'ghost'"])
    assert "rejected by the validator" in captured["user"]
    assert "unknown dependency 'ghost'" in captured["user"]
