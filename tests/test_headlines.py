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

    async def fake_complete(system, user, max_tokens=64):
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
