import os

import pytest

from artel.store import pricing


class TestPricingRefusals:
    def test_subscription_is_never_priced(self):
        r = pricing.cost_usd("claude-opus-5", "subscription", {"output_tokens": 10**6})
        assert r["amount"] is None and "subscription" in r["reason"]

    def test_unknown_model_is_not_zero(self):
        # $0 reads as "this was free", the most expensive way for a spend report to be wrong.
        r = pricing.cost_usd("no-such-model", "metered", {"output_tokens": 10**6})
        assert r["amount"] is None and "no published rate" in r["reason"]

    def test_metered_shows_its_derivation(self, monkeypatch):
        monkeypatch.setenv("MODEL_RATES", '{"m":{"input":1e-6,"output":2e-6}}')
        r = pricing.cost_usd("m", "metered", {"input_tokens": 1000, "output_tokens": 500})
        assert r["amount"] == pytest.approx(0.002)
        assert "1000*1e-06" in r["derivation"].replace(" ", "") or "1000*" in r["derivation"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "u.db"))
    monkeypatch.setenv("AGENT_KEYS", "tester:k1")
    monkeypatch.setenv("MODEL_RATES", '{"m":{"input":1e-6,"output":2e-6}}')
    for mod in [m for m in list(os.sys.modules) if m.startswith("artel.")]:
        del os.sys.modules[mod]
    from fastapi.testclient import TestClient

    import artel.server.app as a

    return TestClient(a.app)


H = {"x-agent-id": "tester", "x-api-key": "k1"}
BODY = {
    "model": "m",
    "session_id": "s1",
    "billing_mode": "metered",
    "turns": 10,
    "input_tokens": 1000,
    "output_tokens": 500,
}


class TestUsageIngest:
    def test_reposting_the_same_window_does_not_double_bill(self, client):
        # A drainer re-reads a transcript from its cursor and re-sends windows it has
        # already shipped. SQLite treats NULLs as DISTINCT in a unique index, so a
        # rollup with no timestamps defeated the dedup and billed twice; the index
        # coalesces window_end for exactly this case.
        for _ in range(3):
            client.post("/usage", json=BODY, headers=H)
        rows = client.get("/usage?days=7", headers=H).json()["rows"]
        assert rows[0]["output_tokens"] == 500

    def test_distinct_windows_accumulate(self, client):
        client.post("/usage", json=BODY, headers=H)
        client.post("/usage", json=dict(BODY, window_end="2026-09-03T10:00:00Z"), headers=H)
        rows = client.get("/usage?days=7", headers=H).json()["rows"]
        assert rows[0]["output_tokens"] == 1000

    def test_total_excludes_what_cannot_be_priced(self, client):
        client.post("/usage", json=BODY, headers=H)
        client.post(
            "/usage",
            json=dict(BODY, session_id="s2", model="opus", billing_mode="subscription"),
            headers=H,
        )
        d = client.get("/usage?days=7", headers=H).json()
        assert d["billable_usd"] == pytest.approx(0.002)
        assert any("subscription" in n for n in d["not_priced"])
