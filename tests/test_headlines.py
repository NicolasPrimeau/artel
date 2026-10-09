import artel.archivist.synthesis as syn


class FakeClient:
    def __init__(self, docs):
        self.docs = docs
        self.calls: list[tuple] = []
        self.logged: list[dict] = []

    async def list_entries(self, type=None, limit=100):
        return [d for d in self.docs if d["type"] == type]

    async def set_headline(self, entry_id, headline, headline_version):
        self.calls.append((entry_id, headline, headline_version))
        return {}

    async def log(self, **kwargs):
        self.logged.append(kwargs)


def _docs():
    return [
        {
            "id": "fresh",
            "type": "doc",
            "content": "body A",
            "version": 2,
            "headline": "already summarized",
            "headline_version": 2,
        },
        {
            "id": "stale",
            "type": "doc",
            "content": "body B",
            "version": 5,
            "headline": "outdated",
            "headline_version": 3,
        },
        {
            "id": "missing",
            "type": "doc",
            "content": "body C",
            "version": 1,
            "headline": None,
            "headline_version": 0,
        },
        {
            "id": "directive",
            "type": "directive",
            "content": "always do X",
            "version": 1,
            "headline": None,
            "headline_version": 0,
        },
    ]


async def _run(monkeypatch, docs, configured=True):
    monkeypatch.setattr(syn, "is_configured", lambda: configured)

    async def fake_complete(system, user, max_tokens=64, strict=False):
        return f"summary of {user}."

    monkeypatch.setattr(syn, "complete", fake_complete)
    client = FakeClient(docs)
    await syn.run_headlines(client)
    return client


async def test_headlines_skip_fresh_regenerate_stale_and_missing(monkeypatch):
    client = await _run(monkeypatch, _docs())
    written = {c[0] for c in client.calls}
    assert "fresh" not in written
    assert written == {"stale", "missing", "directive"}


async def test_headlines_stamp_current_version(monkeypatch):
    client = await _run(monkeypatch, _docs())
    stamped = {c[0]: c[2] for c in client.calls}
    assert stamped["stale"] == 5
    assert stamped["missing"] == 1


async def test_headlines_strip_trailing_punctuation(monkeypatch):
    client = await _run(monkeypatch, _docs())
    assert all(not c[1].endswith(".") for c in client.calls)


async def test_headlines_passive_noop_without_llm(monkeypatch):
    client = await _run(monkeypatch, _docs(), configured=False)
    assert client.calls == []


async def test_truncated_headline_is_retried_then_skipped(monkeypatch):
    from artel.archivist.llm import LLMTruncated

    monkeypatch.setattr(syn, "is_configured", lambda: True)
    attempts = {}

    async def fake_complete(system, user, max_tokens=64, strict=False):
        assert strict
        attempts[user] = attempts.get(user, 0) + 1
        if "always" in user:
            raise LLMTruncated("cut")
        if attempts[user] == 1:
            raise LLMTruncated("cut")
        return f"summary of {user}."

    monkeypatch.setattr(syn, "complete", fake_complete)
    client = FakeClient(_docs())
    await syn.run_headlines(client)

    written = {c[0] for c in client.calls}
    assert written == {"stale", "missing"}
    assert all(n == 2 for n in attempts.values())


def test_truncated_headlines_are_cleared_once(tmp_path):
    import sqlite3

    from artel.store.db import _clear_truncated_headlines

    conn = sqlite3.connect(tmp_path / "h.db")
    conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE memory (id TEXT, headline TEXT, headline_version INTEGER)")
    whole = "Why rolling deploys time out when a volume is attached"
    conn.executemany(
        "INSERT INTO memory VALUES (?,?,?)",
        [("cut", "Query parameters, responses, and city", 3), ("one", ")", 2), ("ok", whole, 4)],
    )
    _clear_truncated_headlines(conn)
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT id, headline, headline_version FROM memory")
    }
    assert rows == {"cut": (None, 0), "one": (None, 0), "ok": (whole, 4)}

    conn.execute("UPDATE memory SET headline = 'short again' WHERE id = 'cut'")
    _clear_truncated_headlines(conn)
    assert conn.execute("SELECT headline FROM memory WHERE id='cut'").fetchone()[0] == "short again"
