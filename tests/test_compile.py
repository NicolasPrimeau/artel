import pytest

from artel.compile import compile_source
from tests.conftest import HEADERS

SRC_V1 = "def g(x):\n    return x + 1\n\n\ndef f(y):\n    return g(y) * 2\n"
SRC_V2 = "def g(x):\n    return x + 100\n\n\ndef f(y):\n    return g(y) * 2\n"


class StubClient:
    def __init__(self, entries=None):
        self.entries = entries or []
        self.patched = []
        self.logs = []

    async def list_entries(self, **kw):
        return self.entries

    async def patch_memory(self, entry_id, **fields):
        self.patched.append((entry_id, fields))
        return {}

    async def log(self, **kw):
        self.logs.append(kw)


def _payload(path, source, project="proj", commit="c1"):
    units = compile_source(path, source)
    return {
        "project": project,
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


async def _join(client, project="proj"):
    await client.post(f"/projects/{project}/join", headers=HEADERS)


@pytest.mark.asyncio
async def test_compile_creates_grounded_memory(client):
    await _join(client)
    r = await client.post("/compile", json=_payload("pkg/m.py", SRC_V1), headers=HEADERS)
    assert r.status_code == 201, r.text
    report = r.json()
    assert report["created"] == 3
    assert report["anchors"] == 3
    assert report["memory_ids"]

    listing = await client.get(
        "/memory", params={"type": "compiled", "project": "proj"}, headers=HEADERS
    )
    rows = listing.json()
    assert len(rows) == 3
    fn = next(m for m in rows if m["source_path"] == "pkg/m.py" and "def f" in m["content"])
    assert fn["type"] == "compiled"
    assert fn["source_sha"]
    assert fn["source_commit"] == "c1"
    assert fn["stale"] is False

    anchors = await client.get("/compile/anchors", params={"project": "proj"}, headers=HEADERS)
    assert {a["symbol"] for a in anchors.json()} == {"", "f", "g"}

    node = await client.get(f"/graph/{fn['id']}", headers=HEADERS)
    g = node.json()
    assert g["kind"] == "memory"
    rels = {e["rel"] for e in g["edges"]["out"]}
    assert "grounds" in rels and "relies_on" in rels
    assert g["viability"]["fresh_grounds"] == 1
    assert g["viability"]["score"] > 0


@pytest.mark.asyncio
async def test_sha_freshness_check(client):
    await _join(client)
    await client.post("/compile", json=_payload("pkg/m.py", SRC_V1), headers=HEADERS)
    units = compile_source("pkg/m.py", SRC_V1)
    g_unit = next(u for u in units if u.symbol == "g")
    g_new = next(u for u in compile_source("pkg/m.py", SRC_V2) if u.symbol == "g")

    check = await client.post(
        "/compile/check",
        json={
            "project": "proj",
            "units": [
                {"path": "pkg/m.py", "symbol": "g", "sha": g_unit.sha},
                {"path": "pkg/m.py", "symbol": "g", "sha": g_new.sha},
                {"path": "pkg/other.py", "symbol": "z", "sha": "deadbeef"},
            ],
        },
        headers=HEADERS,
    )
    statuses = [r["status"] for r in check.json()]
    assert statuses == ["fresh", "stale", "unknown"]


@pytest.mark.asyncio
async def test_invalidation_propagates_along_relies_on(client):
    await _join(client)
    await client.post("/compile", json=_payload("pkg/m.py", SRC_V1), headers=HEADERS)

    units_v2 = compile_source("pkg/m.py", SRC_V2)
    g_only = next(u for u in units_v2 if u.symbol == "g")
    body = {
        "project": "proj",
        "commit": "c2",
        "units": [
            {
                "path": g_only.path,
                "symbol": g_only.symbol,
                "lang": g_only.lang,
                "kind": g_only.kind,
                "start_line": g_only.start_line,
                "end_line": g_only.end_line,
                "sha": g_only.sha,
                "description": g_only.description,
                "deps": [],
            }
        ],
    }
    r = await client.post("/compile", json=body, headers=HEADERS)
    report = r.json()
    assert report["updated"] == 1

    stale = await client.get("/compile/stale", params={"project": "proj"}, headers=HEADERS)
    assert any("def f" in m["content"] for m in stale.json())
    assert report["invalidated"]


@pytest.mark.asyncio
async def test_graph_edge_and_viability(client):
    await _join(client)
    await client.post("/compile", json=_payload("pkg/m.py", SRC_V1), headers=HEADERS)
    rows = (
        await client.get("/memory", params={"type": "compiled", "project": "proj"}, headers=HEADERS)
    ).json()
    a, b = rows[0]["id"], rows[1]["id"]

    edge = await client.post(
        "/graph/edge",
        json={"project": "proj", "src": a, "dst": b, "rel": "corroborates"},
        headers=HEADERS,
    )
    assert edge.status_code == 201

    node = await client.get(f"/graph/{b}", headers=HEADERS)
    assert node.json()["viability"]["backlinks"] == 1
    via_a = await client.get(f"/graph/{a}", headers=HEADERS)
    assert via_a.json()["viability"]["corroborates"] == 1

    missing = await client.post(
        "/graph/edge",
        json={"project": "proj", "src": a, "dst": "nope", "rel": "corroborates"},
        headers=HEADERS,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_compiled_excluded_from_decay(client):
    from artel.archivist.synthesis import decay_confidence

    stub = StubClient(
        [
            {"id": "m1", "type": "memory", "confidence": 0.9, "origin": None},
            {"id": "c1", "type": "compiled", "confidence": 0.9, "origin": None},
        ]
    )
    await decay_confidence(stub)
    patched = {p[0] for p in stub.patched}
    assert "m1" in patched
    assert "c1" not in patched


@pytest.mark.asyncio
async def test_run_compilation_weaves_authored_to_compiled(client):
    from artel.archivist.synthesis import run_compilation

    await _join(client)
    await client.post("/compile", json=_payload("pkg/m.py", SRC_V1), headers=HEADERS)
    await client.post(
        "/memory",
        json={"project": "proj", "content": "heads up: pkg/m.py has a tricky mutual recursion"},
        headers=HEADERS,
    )
    stub = StubClient()
    await run_compilation(stub)

    edges = (
        await client.get("/graph", params={"project": "proj", "rel": "applies_to"}, headers=HEADERS)
    ).json()
    assert len(edges) >= 1
    assert stub.logs and stub.logs[0]["action"] == "compilation"
