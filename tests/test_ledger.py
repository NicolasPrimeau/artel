import pytest


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Fresh store per test.

    Resets the module-level connection rather than deleting artel.* from sys.modules:
    tearing the modules down leaves later tests holding a different module object with
    a stale connection, which made two unrelated suites fail depending on order.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "l.db"))
    monkeypatch.setenv("AGENT_KEYS", "t:k")
    monkeypatch.setenv("MODEL_RATES", '{"m":{"input":1e-6,"output":2e-6}}')
    from artel.ledger import facts as f
    from artel.store import db as dbmod

    monkeypatch.setattr(dbmod, "_conn", None)
    d = dbmod.get_db()
    with d:
        d.execute(
            """INSERT INTO usage_events (id, agent_id, project, session_id, model,
               billing_mode, turns, input_tokens, output_tokens, cache_read, cache_write)
               VALUES ('u1','a','p1','s1','m','subscription',10,1000,500,0,0)"""
        )
        d.execute(
            """INSERT INTO decisions (id, project, agent_id, decision, rationale,
               alternatives, session_id)
               VALUES ('d1','p1','a','chose m','because','["n"]','s1')"""
        )
    return f, d


class TestLedgerFacts:
    def test_project_rollup_prices_seat_work_as_an_equivalent(self, ledger):
        facts, _ = ledger
        rows = facts.by_project(30)
        assert rows[0]["project"] == "p1"
        assert rows[0]["equivalent"] == pytest.approx(0.002)
        assert rows[0]["billed"] == 0.0
        assert rows[0]["decisions"] == 1

    def test_decision_carries_the_cost_of_its_session(self, ledger):
        facts, _ = ledger
        rows = facts.by_decision(30)
        assert rows[0]["decision"] == "chose m"
        assert rows[0]["session_cost"] == pytest.approx(0.002)

    def test_a_decision_with_no_session_is_not_priced(self, ledger):
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO decisions (id, project, agent_id, decision, rationale,
                   alternatives) VALUES ('d2','p1','a','orphan','x','[]')"""
            )
        orphan = next(r for r in facts.by_decision(30) if r["id"] == "d2")
        # None, not 0.0 — a decision we cannot cost must not read as a free one.
        assert orphan["session_cost"] is None

    def test_totals_keep_invoice_and_valuation_apart(self, ledger):
        facts, _ = ledger
        t = facts.totals(30)
        assert t["billed"] == 0.0 and t["equivalent"] == pytest.approx(0.002)
