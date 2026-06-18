"""
Compile-mode scenarios.

Realistic lifecycles for source-grounded memory: an agent compiles a repo, the
code drifts under it, a teammate detects the staleness without re-reading the
code, and the two memory modes catch a contradiction between them.
"""

SRC_V1 = (
    "def validate(req):\n"
    "    return bool(req)\n"
    "\n"
    "\n"
    "def handle(req):\n"
    "    if validate(req):\n"
    "        return 200\n"
    "    return 400\n"
)

SRC_V2 = (
    "def validate(req):\n"
    '    return bool(req) and "id" in req\n'
    "\n"
    "\n"
    "def handle(req):\n"
    "    if validate(req):\n"
    "        return 200\n"
    "    return 400\n"
)


def _unit(source, symbol):
    from artel.compile import compile_source

    u = next(u for u in compile_source("svc/api.py", source) if u.symbol == symbol)
    return {
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


async def test_compile_drift_is_detected_by_a_teammate_without_rereading_code(scenario):
    builder = await scenario.agent("builder")
    reviewer = await scenario.agent("reviewer")
    await builder.join_project("svc")
    await reviewer.join_project("svc")

    report = await builder.compile("svc/api.py", SRC_V1, "c1", project="svc")
    assert report["created"] == 3

    compiled = await reviewer.list_memory(type="compiled", project="svc")
    assert {m["source_path"] for m in compiled} == {"svc/api.py"}
    handler = next(m for m in compiled if "def handle" in m["content"])
    module = next(m for m in compiled if "SHAPE" in m["content"])
    assert handler["stale"] is False

    fresh = await reviewer.compile_check("svc", [_unit(SRC_V1, "handle")])
    assert fresh[0]["status"] == "fresh"

    # validate's body changes; builder recompiles ONLY validate (a partial commit).
    resp = await builder._http.post(
        "/compile",
        json={"project": "svc", "commit": "c2", "units": [_unit(SRC_V2, "validate")]},
    )
    assert resp.status_code == 201
    assert resp.json()["invalidated"]

    # handler never changed, but it relies_on validate — so its grounded note is now stale.
    stale = await reviewer.compile_stale(project="svc")
    stale_ids = {m["id"] for m in stale}
    assert handler["id"] in stale_ids
    assert module["id"] not in stale_ids  # module shape is unchanged

    v = (await reviewer.graph_node(handler["id"]))["viability"]
    assert v["stale_grounds"] >= 1
    assert v["score"] == 0.0

    # recompiling the whole file clears the staleness.
    report = await builder.compile("svc/api.py", SRC_V2, "c3", project="svc")
    assert report["invalidated"] == []
    refreshed = await reviewer.graph_node(handler["id"])
    assert refreshed["node"]["stale"] == 0
    assert refreshed["viability"]["fresh_grounds"] == 1
    assert refreshed["viability"]["score"] > 0


async def test_authored_memory_contradicting_compiled_collapses_both(scenario):
    builder = await scenario.agent("builder")
    ops = await scenario.agent("ops")
    await builder.join_project("svc")
    await ops.join_project("svc")

    await builder.compile("svc/api.py", SRC_V1, "c1", project="svc")
    compiled = await ops.list_memory(type="compiled", project="svc")
    handler = next(m for m in compiled if "def handle" in m["content"])

    base = (await ops.graph_node(handler["id"]))["viability"]
    assert base["score"] > 0

    note = await ops.write_memory(
        "handle() must stay offline-pure — it never calls the network", project="svc"
    )
    await ops.graph_edge(note["id"], handler["id"], "contradicts", project="svc")

    vh = (await ops.graph_node(handler["id"]))["viability"]
    vn = (await ops.graph_node(note["id"]))["viability"]
    assert vh["contradictions"] == 1
    assert vn["contradictions"] == 1
    assert vh["score"] < base["score"]
