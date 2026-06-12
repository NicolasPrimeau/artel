from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import HEADERS, HEADERS2


def _payload(content, confidence=1.0, tags=None, **kwargs):
    return {
        "content": content,
        "type": "memory",
        "scope": "project",
        "tags": tags or [],
        "parents": [],
        "confidence": confidence,
        **kwargs,
    }


def _v(*coords):
    vec = [0.0] * 384
    for i, c in enumerate(coords):
        vec[i] = c
    return vec


def _embed_table(table, default=None):
    def _embed(text):
        low = text.lower()
        for key, vec in table.items():
            if key in low:
                return vec
        return default

    return _embed


def _patch_embed(monkeypatch, fn):
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", fn)


def _backdate(entry_id, days):
    import artel.store.db as db_mod

    db = db_mod.get_db()
    stamp = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with db:
        db.execute("UPDATE memory SET updated_at=? WHERE id=?", (stamp, entry_id))


async def test_search_degrades_to_keyword_when_embeddings_down(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post("/memory", json=_payload("the fly deploy token rotated"), headers=HEADERS)
    await client.post("/memory", json=_payload("unrelated grocery list"), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "deploy token"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    assert results[0]["content"] == "the fly deploy token rotated"


async def test_hybrid_ranks_exact_token_match_first(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: [0.0] * 384)

    await client.post("/memory", json=_payload("phalanx zone damage doubled"), headers=HEADERS)
    await client.post("/memory", json=_payload("watchtower incident drill notes"), headers=HEADERS)
    await client.post("/memory", json=_payload("automata tribe seeding logic"), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "phalanx zone"}, headers=HEADERS)
    results = r.json()
    assert results
    assert results[0]["content"] == "phalanx zone damage doubled"


async def test_rrf_prefers_dual_signal_match(client, monkeypatch):
    # "alpha briefing" matches both rankers, "alpha checklist" is keyword-only
    # (written while embeddings were down), "morning briefing notes" is vector-only.
    table = {
        "alpha briefing": _v(1.0, 0.01),
        "morning briefing": _v(1.0, 0.02),
        "alpha checklist": None,
        "alpha": _v(1.0),
    }
    _patch_embed(monkeypatch, _embed_table(table, default=_v(0.0, 90.0)))

    for content in ("alpha briefing", "alpha checklist", "morning briefing notes"):
        await client.post("/memory", json=_payload(content), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "alpha"}, headers=HEADERS)
    results = [e["content"] for e in r.json()]
    assert results[0] == "alpha briefing"
    assert set(results) == {"alpha briefing", "alpha checklist", "morning briefing notes"}


async def test_ranking_boosts_confidence(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post(
        "/memory", json=_payload("release checklist draft", confidence=0.1), headers=HEADERS
    )
    await client.post(
        "/memory", json=_payload("release checklist final", confidence=1.0), headers=HEADERS
    )

    r = await client.get("/memory/search", params={"q": "release checklist"}, headers=HEADERS)
    results = r.json()
    assert len(results) == 2
    assert results[0]["content"] == "release checklist final"


async def test_ranking_boosts_read_count(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    r1 = await client.post("/memory", json=_payload("supply cache at ridge"), headers=HEADERS)
    r2 = await client.post("/memory", json=_payload("supply cache at river"), headers=HEADERS)
    assert r1.status_code == 201

    for _ in range(8):
        await client.get(f"/memory/{r2.json()['id']}", headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "supply cache"}, headers=HEADERS)
    assert r.json()[0]["content"] == "supply cache at river"


async def test_ranking_boosts_recency(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    stale = await client.post("/memory", json=_payload("patrol route over pass"), headers=HEADERS)
    await client.post("/memory", json=_payload("patrol route through valley"), headers=HEADERS)
    _backdate(stale.json()["id"], days=180)

    r = await client.get("/memory/search", params={"q": "patrol route"}, headers=HEADERS)
    assert r.json()[0]["content"] == "patrol route through valley"


async def test_ancient_entries_still_surface(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    old = await client.post(
        "/memory", json=_payload("legacy quartermaster ledger"), headers=HEADERS
    )
    _backdate(old.json()["id"], days=5 * 365)

    r = await client.get("/memory/search", params={"q": "quartermaster"}, headers=HEADERS)
    assert [e["id"] for e in r.json()] == [old.json()["id"]]


async def test_soft_deleted_entry_leaves_keyword_search(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    w = await client.post("/memory", json=_payload("ephemeral scaffolding note"), headers=HEADERS)
    entry_id = w.json()["id"]

    r = await client.get("/memory/search", params={"q": "scaffolding"}, headers=HEADERS)
    assert len(r.json()) == 1

    assert (await client.delete(f"/memory/{entry_id}", headers=HEADERS)).status_code == 204
    r = await client.get("/memory/search", params={"q": "scaffolding"}, headers=HEADERS)
    assert r.json() == []


async def test_bulk_deleted_entries_leave_keyword_search(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    ids = []
    for content in ("orphan alpha shard", "orphan beta shard"):
        w = await client.post("/memory", json=_payload(content), headers=HEADERS)
        ids.append(w.json()["id"])

    r = await client.request("DELETE", "/memory", json={"ids": ids}, headers=HEADERS)
    assert r.status_code == 204

    r = await client.get("/memory/search", params={"q": "shard"}, headers=HEADERS)
    assert r.json() == []


@pytest.mark.parametrize(
    "query",
    [
        "AND OR NOT NEAR",
        '"quoted phrase" alpha',
        "alpha-beta!",
        "what's*this?",
        "café résumé",
        "(((alpha)))",
        "alpha AND beta OR gamma",
    ],
)
async def test_keyword_search_survives_operator_and_punctuation_queries(client, monkeypatch, query):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post("/memory", json=_payload("alpha beta gamma café résumé"), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": query}, headers=HEADERS)
    assert r.status_code == 200


async def test_no_tokens_and_no_embeddings_returns_empty(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post("/memory", json=_payload("something findable"), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "!!! ??? ..."}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == []


async def test_long_query_is_token_capped_but_works(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post("/memory", json=_payload("needle in the haystack"), headers=HEADERS)

    query = "needle " + " ".join(f"filler{i}" for i in range(40))
    r = await client.get("/memory/search", params={"q": query}, headers=HEADERS)
    assert [e["content"] for e in r.json()] == ["needle in the haystack"]


async def test_keyword_path_applies_tag_type_agent_and_confidence_filters(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post(
        "/memory", json=_payload("tagged convoy schedule", tags=["ops"]), headers=HEADERS
    )
    await client.post("/memory", json=_payload("untagged convoy schedule"), headers=HEADERS)
    await client.post(
        "/memory", json=_payload("convoy schedule guess", confidence=0.2), headers=HEADERS
    )
    await client.post("/memory", json=_payload("convoy schedule from two"), headers=HEADERS2)

    r = await client.get("/memory/search", params={"q": "convoy", "tag": "ops"}, headers=HEADERS)
    assert [e["content"] for e in r.json()] == ["tagged convoy schedule"]

    r = await client.get(
        "/memory/search", params={"q": "convoy", "confidence_min": 0.5}, headers=HEADERS
    )
    assert all(e["confidence"] >= 0.5 for e in r.json())
    assert "convoy schedule guess" not in [e["content"] for e in r.json()]

    r = await client.get(
        "/memory/search", params={"q": "convoy", "agent": "otheragent"}, headers=HEADERS
    )
    assert [e["content"] for e in r.json()] == ["convoy schedule from two"]

    r = await client.get(
        "/memory/search", params={"q": "convoy", "type": "memory", "limit": 2}, headers=HEADERS
    )
    assert len(r.json()) == 2


async def test_keyword_path_respects_project_filter(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    await client.post("/projects", json={"name": "proj-x"}, headers=HEADERS)
    await client.post("/projects", json={"name": "proj-y"}, headers=HEADERS)
    await client.post("/memory", json=_payload("turbine manual", project="proj-x"), headers=HEADERS)
    await client.post(
        "/memory", json=_payload("turbine manual revision", project="proj-y"), headers=HEADERS
    )

    r = await client.get(
        "/memory/search", params={"q": "turbine", "project": "proj-x"}, headers=HEADERS
    )
    assert [e["content"] for e in r.json()] == ["turbine manual"]


async def test_max_distance_keeps_only_close_vector_hits(client, monkeypatch):
    table = {
        "close match entry": _v(1.0, 0.05),
        "far match entry": _v(0.0, 50.0),
        "match query": _v(1.0),
        "keyword only entry": None,
    }
    _patch_embed(monkeypatch, _embed_table(table, default=_v(0.0, 90.0)))

    for content in ("close match entry", "far match entry", "keyword only entry match"):
        await client.post("/memory", json=_payload(content), headers=HEADERS)

    r = await client.get(
        "/memory/search", params={"q": "match query", "max_distance": 1.0}, headers=HEADERS
    )
    assert [e["content"] for e in r.json()] == ["close match entry"]

    r = await client.get("/memory/search", params={"q": "match query"}, headers=HEADERS)
    assert len(r.json()) == 3


async def test_fts_backfill_indexes_preexisting_rows(client, monkeypatch):
    import artel.server.config as cfg_mod
    import artel.store.db as db_mod

    _patch_embed(monkeypatch, lambda text: None)

    db = db_mod.get_db()
    now = datetime.now(UTC).isoformat()
    with db:
        db.execute(
            """INSERT INTO memory (id, type, agent_id, scope, content, confidence,
               parents, tags, created_at, updated_at)
               VALUES ('legacy-row-1', 'memory', 'testagent', 'project',
                       'pre-fts heritage entry', 1.0, '[]', '[]', ?, ?)""",
            (now, now),
        )
        db.execute("DELETE FROM memory_fts WHERE id='legacy-row-1'")

    r = await client.get("/memory/search", params={"q": "heritage"}, headers=HEADERS)
    assert r.json() == []

    db_mod._conn.close()
    db_mod._conn = None
    db_mod.get_db(cfg_mod.settings.db_path)

    r = await client.get("/memory/search", params={"q": "heritage"}, headers=HEADERS)
    assert [e["id"] for e in r.json()] == ["legacy-row-1"]


async def test_patch_reindexes_keyword_search(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    w = await client.post("/memory", json=_payload("original wording here"), headers=HEADERS)
    entry_id = w.json()["id"]

    await client.patch(
        f"/memory/{entry_id}", json={"content": "zanzibar replacement text"}, headers=HEADERS
    )

    r = await client.get("/memory/search", params={"q": "zanzibar"}, headers=HEADERS)
    assert [e["id"] for e in r.json()] == [entry_id]

    r = await client.get("/memory/search", params={"q": "original wording"}, headers=HEADERS)
    assert all(e["id"] != entry_id for e in r.json())


async def test_patch_during_outage_makes_entry_semantic_after_recovery(client, monkeypatch):
    table = {
        "rendezvous": _v(1.0, 0.01),
        "regroup": _v(1.0),
    }
    state = {"down": True}

    def switchable(text):
        return None if state["down"] else _embed_table(table, default=_v(0.0, 90.0))(text)

    _patch_embed(monkeypatch, switchable)

    w = await client.post(
        "/memory", json=_payload("fallback rendezvous at north bridge"), headers=HEADERS
    )
    entry_id = w.json()["id"]

    r = await client.get("/memory/search", params={"q": "where do we regroup"}, headers=HEADERS)
    assert r.json() == []

    state["down"] = False
    r = await client.get("/memory/search", params={"q": "where do we regroup"}, headers=HEADERS)
    assert r.json() == []

    await client.patch(
        f"/memory/{entry_id}",
        json={"content": "fallback rendezvous at north bridge"},
        headers=HEADERS,
    )
    r = await client.get("/memory/search", params={"q": "where do we regroup"}, headers=HEADERS)
    assert [e["id"] for e in r.json()] == [entry_id]


async def test_search_keyword_respects_agent_scope(client, monkeypatch):
    _patch_embed(monkeypatch, lambda text: None)

    body = _payload("secret xylophone plan")
    body["scope"] = "agent"
    await client.post("/memory", json=body, headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "xylophone"}, headers=HEADERS)
    assert len(r.json()) == 1

    r = await client.get("/memory/search", params={"q": "xylophone"}, headers=HEADERS2)
    assert r.json() == []


async def test_search_vector_respects_agent_scope(client, monkeypatch):
    _patch_embed(monkeypatch, _embed_table({"glockenspiel": _v(1.0)}, default=_v(0.0, 90.0)))

    body = _payload("private glockenspiel notes")
    body["scope"] = "agent"
    await client.post("/memory", json=body, headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "glockenspiel"}, headers=HEADERS)
    assert len(r.json()) == 1

    r = await client.get("/memory/search", params={"q": "glockenspiel"}, headers=HEADERS2)
    assert r.json() == []


async def test_health_reports_embeddings_status(client, monkeypatch):
    import artel.server.app as app_mod

    monkeypatch.setattr(app_mod, "embeddings_ok", lambda: True)
    r = await client.get("/health")
    assert r.json() == {"status": "ok", "embeddings": "ok"}

    monkeypatch.setattr(app_mod, "embeddings_ok", lambda: False)
    r = await client.get("/health")
    assert r.json() == {"status": "ok", "embeddings": "unavailable"}
