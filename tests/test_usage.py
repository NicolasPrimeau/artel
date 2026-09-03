import os

import pytest

from artel.store import pricing


class TestPricingRefusals:
    def test_subscription_is_priced_as_an_equivalent_not_an_invoice(self, monkeypatch):
        # The tokens are identical either way, and on a seat the list-price number is
        # what the seat is worth — useful rather than fiction, so long as it is labelled.
        monkeypatch.setenv("MODEL_RATES", '{"m":{"input":1e-6,"output":2e-6}}')
        r = pricing.cost_usd("m", "subscription", {"output_tokens": 10**6})
        assert r["amount"] == pytest.approx(2.0)
        assert r["billed"] is False and r["basis"] == "list-price equivalent"

    def test_metered_is_labelled_as_actual_spend(self, monkeypatch):
        monkeypatch.setenv("MODEL_RATES", '{"m":{"input":1e-6,"output":2e-6}}')
        r = pricing.cost_usd("m", "metered", {"output_tokens": 10**6})
        assert r["billed"] is True and r["basis"] == "actual spend"

    def test_claude_code_model_ids_resolve(self, monkeypatch):
        # A session records claude-opus-4-8; rate tables name it anthropic/claude-opus-4.8.
        monkeypatch.setenv(
            "MODEL_RATES", '{"anthropic/claude-opus-4.8":{"input":1e-6,"output":2e-6}}'
        )
        assert pricing.rate_for("claude-opus-4-8") is not None
        assert pricing.rate_for("claude-opus-4-8-20260101") is not None

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

    def test_invoice_and_equivalent_are_reported_separately(self, client):
        # Same model and rate, different billing. One is an invoice, one is what the
        # seat is worth; both are useful and adding them together is not.
        client.post("/usage", json=BODY, headers=H)
        client.post(
            "/usage",
            json=dict(BODY, session_id="s2", billing_mode="subscription"),
            headers=H,
        )
        d = client.get("/usage?days=7", headers=H).json()
        assert d["billed_usd"] == pytest.approx(0.002)
        assert d["list_equivalent_usd"] == pytest.approx(0.002)
