"""Hybrid-retrieval scenarios: a fleet coordinating through memory search while
the embedding model comes and goes. These lock in that:
- keyword search keeps the fleet sharing intel during an embeddings outage
- recovery restores semantic search, and updating an entry backfills its vector
- updated knowledge supersedes old wording for every agent, not just the author
- well-read and high-confidence entries outrank stale guesses
- agent-private notes and project boundaries hold on BOTH retrieval paths
- clearing a project removes its entries from search
"""

import pytest


def _v(*coords):
    vec = [0.0] * 384
    for i, c in enumerate(coords):
        vec[i] = c
    return vec


class EmbedSwitch:
    def __init__(self, table=None):
        self.table = table or {}
        self.down = False

    def __call__(self, text):
        if self.down:
            return None
        low = text.lower()
        for key, vec in self.table.items():
            if key in low:
                return vec
        return _v(0.0, 90.0)


@pytest.fixture
def embed_switch(monkeypatch):
    import artel.server.app as app_mod
    import artel.server.routes.memory as mem_routes

    switch = EmbedSwitch()
    monkeypatch.setattr(mem_routes, "embed", switch)
    monkeypatch.setattr(app_mod, "embeddings_ok", lambda: not switch.down)
    return switch


async def test_fleet_shares_intel_by_keyword_during_outage(scenario, embed_switch):
    embed_switch.down = True
    scout = await scenario.agent("scout")
    gunner = await scenario.agent("gunner")
    await scout.join_project("recon")
    await gunner.join_project("recon")

    await scout.write_memory("enemy armor spotted at grid 7-3", project="recon")
    await scout.write_memory("supply road clear toward bridge", project="recon")

    results = await gunner.search_memory("armor grid", project="recon")
    assert results
    assert results[0]["content"] == "enemy armor spotted at grid 7-3"

    health = await gunner._http.get("/health")
    assert health.json()["embeddings"] == "unavailable"


async def test_outage_degrades_then_recovery_restores_semantic_search(scenario, embed_switch):
    embed_switch.table = {
        "rendezvous": _v(1.0, 0.01),
        "regroup": _v(1.0),
    }
    embed_switch.down = True

    leader = await scenario.agent("leader")
    wingman = await scenario.agent("wingman")
    await leader.join_project("squad")
    await wingman.join_project("squad")

    entry = await leader.write_memory("fallback rendezvous at north bridge", project="squad")

    assert await wingman.search_memory("rendezvous", project="squad")
    assert await wingman.search_memory("where do we regroup", project="squad") == []
    assert (await wingman._http.get("/health")).json()["embeddings"] == "unavailable"

    embed_switch.down = False
    assert (await wingman._http.get("/health")).json()["embeddings"] == "ok"
    assert await wingman.search_memory("where do we regroup", project="squad") == []

    await leader.update_memory(entry["id"], content="fallback rendezvous at north bridge")
    results = await wingman.search_memory("where do we regroup", project="squad")
    assert [e["id"] for e in results] == [entry["id"]]


async def test_updated_knowledge_supersedes_old_wording_for_everyone(scenario, embed_switch):
    embed_switch.down = True
    keeper = await scenario.agent("keeper")
    reader = await scenario.agent("reader")
    await keeper.join_project("ops")
    await reader.join_project("ops")

    entry = await keeper.write_memory("rally point is south tower", project="ops")
    assert [e["id"] for e in await reader.search_memory("south tower", project="ops")] == [
        entry["id"]
    ]

    await keeper.update_memory(entry["id"], content="rally point moved to east gate")

    assert [e["id"] for e in await reader.search_memory("east gate", project="ops")] == [
        entry["id"]
    ]
    assert all(
        e["id"] != entry["id"] for e in await reader.search_memory("south tower", project="ops")
    )


async def test_well_read_lessons_circulate_first(scenario, embed_switch):
    embed_switch.down = True
    veteran = await scenario.agent("veteran")
    await veteran.join_project("lessons")

    await veteran.write_memory("lesson: focus fire scatters fast", project="lessons")
    popular = await veteran.write_memory("lesson: focus fire wins fights", project="lessons")

    for name in ("rookie-1", "rookie-2", "rookie-3"):
        rookie = await scenario.agent(name)
        await rookie.join_project("lessons")
        for _ in range(3):
            await rookie.get_memory(popular["id"])

    results = await veteran.search_memory("focus fire lesson", project="lessons")
    assert results[0]["id"] == popular["id"]


async def test_confident_facts_outrank_guesses(scenario, embed_switch):
    embed_switch.down = True
    analyst = await scenario.agent("analyst")
    await analyst.join_project("intel")

    await analyst.write_memory("enemy base maybe behind ridge", project="intel", confidence=0.2)
    fact = await analyst.write_memory(
        "enemy base confirmed behind ridge", project="intel", confidence=1.0
    )

    results = await analyst.search_memory("enemy base ridge", project="intel")
    assert results[0]["id"] == fact["id"]

    high_only = await analyst.search_memory("enemy base ridge", project="intel", confidence_min=0.5)
    assert [e["id"] for e in high_only] == [fact["id"]]


async def test_private_notes_stay_private_on_both_paths(scenario, embed_switch):
    embed_switch.table = {"glockenspiel": _v(1.0)}
    author = await scenario.agent("author")
    snoop = await scenario.agent("snoop")
    await author.join_project("shared")
    await snoop.join_project("shared")

    await author.write_memory(
        "private glockenspiel rehearsal notes", project="shared", scope="agent"
    )

    assert len(await author.search_memory("glockenspiel", project="shared")) == 1
    assert await snoop.search_memory("glockenspiel", project="shared") == []

    embed_switch.down = True
    assert len(await author.search_memory("glockenspiel", project="shared")) == 1
    assert await snoop.search_memory("glockenspiel", project="shared") == []


async def test_project_boundaries_hold_for_keyword_search(scenario, embed_switch):
    embed_switch.down = True
    insider = await scenario.agent("insider")
    outsider = await scenario.agent("outsider")
    await insider.join_project("vault")
    await outsider.join_project("elsewhere")

    await insider.write_memory("vault combination rotated", project="vault")

    assert await insider.search_memory("vault combination", project="vault")
    assert await outsider.search_memory("vault combination", project="vault") == []
    assert await outsider.search_memory("vault combination") == []


async def test_project_clear_removes_entries_from_search(scenario, embed_switch):
    embed_switch.down = True
    owner = await scenario.agent("arena-owner")
    await owner.join_project("arena")

    await owner.write_memory("arena obstacle layout memo", project="arena")
    assert await owner.search_memory("obstacle layout", project="arena")

    assert (await owner.clear_project("arena")).status_code == 204
    assert await owner.search_memory("obstacle layout", project="arena") == []


async def test_hybrid_surfaces_semantic_and_keyword_matches_together(scenario, embed_switch):
    embed_switch.table = {
        "deployment runs on fly": _v(1.0, 0.01),
        "where do we host": _v(1.0),
    }
    devops = await scenario.agent("devops")
    await devops.join_project("infra")

    await devops.write_memory("deployment runs on fly machines", project="infra")
    await devops.write_memory("host header rules for the proxy", project="infra")

    results = await devops.search_memory("where do we host", project="infra")
    contents = [e["content"] for e in results]
    assert "deployment runs on fly machines" in contents
    assert "host header rules for the proxy" in contents


async def test_mid_session_outage_keeps_existing_corpus_searchable(scenario, embed_switch):
    embed_switch.table = {"telemetry": _v(1.0, 0.01), "metrics": _v(1.0)}
    sre = await scenario.agent("sre")
    await sre.join_project("observability")

    await sre.write_memory("telemetry pipeline backfilled", project="observability")
    assert await sre.search_memory("metrics", project="observability")

    embed_switch.down = True
    results = await sre.search_memory("telemetry", project="observability")
    assert [e["content"] for e in results] == ["telemetry pipeline backfilled"]
