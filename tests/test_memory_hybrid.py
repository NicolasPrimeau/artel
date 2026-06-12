from tests.conftest import HEADERS


def _payload(content, confidence=1.0, tags=None):
    return {
        "content": content,
        "type": "memory",
        "scope": "project",
        "tags": tags or [],
        "parents": [],
        "confidence": confidence,
    }


async def test_search_degrades_to_keyword_when_embeddings_down(client, monkeypatch):
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", lambda text: None)

    await client.post("/memory", json=_payload("the fly deploy token rotated"), headers=HEADERS)
    await client.post("/memory", json=_payload("unrelated grocery list"), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "deploy token"}, headers=HEADERS)
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    assert results[0]["content"] == "the fly deploy token rotated"


async def test_hybrid_ranks_exact_token_match_first(client, monkeypatch):
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", lambda text: [0.0] * 384)

    await client.post("/memory", json=_payload("phalanx zone damage doubled"), headers=HEADERS)
    await client.post("/memory", json=_payload("watchtower incident drill notes"), headers=HEADERS)
    await client.post("/memory", json=_payload("automata tribe seeding logic"), headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "phalanx zone"}, headers=HEADERS)
    results = r.json()
    assert results
    assert results[0]["content"] == "phalanx zone damage doubled"


async def test_ranking_boosts_confidence(client, monkeypatch):
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", lambda text: None)

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


async def test_patch_reindexes_keyword_search(client, monkeypatch):
    import artel.server.routes.memory as mem_routes

    monkeypatch.setattr(mem_routes, "embed", lambda text: None)

    w = await client.post("/memory", json=_payload("original wording here"), headers=HEADERS)
    entry_id = w.json()["id"]

    await client.patch(
        f"/memory/{entry_id}", json={"content": "zanzibar replacement text"}, headers=HEADERS
    )

    r = await client.get("/memory/search", params={"q": "zanzibar"}, headers=HEADERS)
    assert [e["id"] for e in r.json()] == [entry_id]

    r = await client.get("/memory/search", params={"q": "original wording"}, headers=HEADERS)
    assert all(e["id"] != entry_id for e in r.json())


async def test_search_keyword_respects_agent_scope(client, monkeypatch):
    import artel.server.routes.memory as mem_routes
    from tests.conftest import HEADERS2

    monkeypatch.setattr(mem_routes, "embed", lambda text: None)

    body = _payload("secret xylophone plan")
    body["scope"] = "agent"
    await client.post("/memory", json=body, headers=HEADERS)

    r = await client.get("/memory/search", params={"q": "xylophone"}, headers=HEADERS)
    assert len(r.json()) == 1

    r = await client.get("/memory/search", params={"q": "xylophone"}, headers=HEADERS2)
    assert r.json() == []


async def test_health_reports_embeddings_status(client, monkeypatch):
    import artel.server.app as app_mod

    monkeypatch.setattr(app_mod, "embeddings_ok", lambda: True)
    r = await client.get("/health")
    assert r.json() == {"status": "ok", "embeddings": "ok"}

    monkeypatch.setattr(app_mod, "embeddings_ok", lambda: False)
    r = await client.get("/health")
    assert r.json() == {"status": "ok", "embeddings": "unavailable"}
