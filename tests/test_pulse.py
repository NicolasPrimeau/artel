import json

from artel.store import graph, hebbian

from .conftest import HEADERS


async def _seed(client):
    import artel.store.db as db_mod

    ids = {}
    for name, content in (
        ("a", "postgres tuning notes"),
        ("b", "postgres is our main store"),
        ("c", "backup runs nightly"),
    ):
        r = await client.post("/memory", json={"content": content}, headers=HEADERS)
        ids[name] = r.json()["id"]
    db = db_mod.get_db()
    graph.add_edge(db, None, ids["a"], ids["b"], "corroborates")
    hebbian.reinforce(db, [ids["a"], ids["c"]])
    db.execute(
        "UPDATE memory SET trail=6, trail_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (ids["a"],),
    )
    db.execute(
        "INSERT INTO task_affinity (agent_id, tag, weight, updated_at)"
        " VALUES ('worker','db',0.6,strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
    )
    db.execute(
        "INSERT INTO memory (id, type, agent_id, content, tags, parents) VALUES"
        " ('sib1','memory','peer','the losing version', ?, ?)",
        (json.dumps(["sync-conflict"]), json.dumps([ids["a"]])),
    )
    db.commit()
    return ids


async def test_pulse_aggregates_all_emergent_state(client):
    ids = await _seed(client)
    r = await client.get("/pulse", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()

    node_ids = {n["id"] for n in d["graph"]["nodes"]}
    assert {ids["a"], ids["b"], ids["c"]} <= node_ids
    kinds = {e["kind"] for e in d["graph"]["edges"]}
    assert kinds == {"semantic", "hebbian"}
    assert d["counts"]["semantic_edges"] == 1
    assert d["counts"]["hebbian_edges"] == 1

    assert d["trails"][0]["id"] == ids["a"]
    assert d["trails"][0]["trail"] > 5

    assert [c["id"] for c in d["central"]][0] == ids["b"]  # corroborated → most central

    assert d["affinities"] == [{"agent_id": "worker", "tag": "db", "weight": 0.6}]

    assert len(d["conflicts"]) == 1
    assert d["conflicts"][0]["id"] == "sib1"
    assert d["conflicts"][0]["parents"] == [ids["a"]]


async def test_pulse_scopes_to_project(client):
    await _seed(client)
    await client.post("/projects/otherproj/join", headers=HEADERS)
    r = await client.get("/pulse", params={"project": "otherproj"}, headers=HEADERS)
    d = r.json()
    assert d["graph"]["nodes"] == []
    assert d["trails"] == []
    assert d["conflicts"] == []


async def test_pulse_requires_auth(client):
    r = await client.get("/pulse")
    assert r.status_code in (401, 403)


async def test_pulse_empty_instance(client):
    r = await client.get("/pulse", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["graph"] == {"nodes": [], "edges": []}
    assert d["counts"]["nodes"] == 0
