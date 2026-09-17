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

    def test_daily_splits_spend_by_day_and_project(self, ledger):
        facts, _ = ledger
        rows = facts.daily(30)
        assert len(rows) == 1
        assert rows[0]["project"] == "p1"
        assert len(rows[0]["day"]) == 10
        assert rows[0]["equivalent"] == pytest.approx(0.002)
        assert rows[0]["billed"] == 0.0

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


class TestToil:
    def test_finds_hand_run_work_and_cites_the_note(self, ledger):
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m1','memory','a','p1',
                   'The sample views were created manually and are not managed by sync.',1.0)"""
            )
        rows = facts.toil(30, "p1")
        assert rows and rows[0]["entry_id"] == "m1"
        assert "manually" in rows[0]["evidence"]

    def test_does_not_fire_on_must_be_resolved(self, ledger):
        # "must be re-\\w+" matched "must be resolved", which is not repeated work.
        # A false candidate is worse than a missed one: it sends someone to automate
        # something that was never manual.
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m2','memory','a','p1',
                   'The cursor must be resolved before the sort branch executes.',1.0)"""
            )
        assert not [r for r in facts.toil(30, "p1") if r["entry_id"] == "m2"]

    def test_unclassified_sorts_last(self, ledger):
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m3','memory','a','p1',
                   'Deployments must be re-run manually after every migration.',1.0)"""
            )
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m4','memory','a','p1',
                   'Somebody has to re-do the widget by hand each time regardless.',1.0)"""
            )
        themes = [t["theme"] for t in facts.toil_themes(30, "p1")]
        if "other" in themes:
            assert themes[-1] == "other"

    def test_instruction_not_to_do_it_by_hand_is_not_toil(self, ledger):
        # "do not manually edit coverage" was counted as evidence of manual work.
        # It is the opposite: a rule saying a script owns the file.
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m5','memory','a','p1',
                   'scripts/coverage --check verifies sync; do not manually edit coverage.',1.0)"""
            )
        assert not [r for r in facts.toil(30, "p1") if r["entry_id"] == "m5"]

    def test_describing_working_automation_is_not_toil(self, ledger):
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m6','memory','a','p1',
                   'The FTS index is kept in sync at every write path, with backfill on startup.',1.0)"""
            )
        assert not [r for r in facts.toil(30, "p1") if r["entry_id"] == "m6"]

    def test_labour_still_counts_when_the_sentence_also_names_automation(self, ledger):
        # Suppressing any mention of automation threw away the strongest evidence in
        # the corpus. These sentences name automation precisely to say it does not
        # cover the case -- which is what makes them worth automating.
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m7','memory','a','p1',
                   'Deployments do not run migrations automatically; they must be applied manually.',1.0)"""
            )
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m8','memory','a','p1',
                   'Registries that auto-discover stay stale until this is done manually.',1.0)"""
            )
        found = {r["entry_id"] for r in facts.toil(30, "p1")}
        assert {"m7", "m8"} <= found

    def test_repetition_alone_is_not_labour(self, ledger):
        # "every time" describes any recurrence, not hand-work: it was dragging in
        # prose like "equal-weight won every time".
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m9','memory','a','p1',
                   'In crypto and stock-momentum baskets, equal-weight won every time.',1.0)"""
            )
        assert not [r for r in facts.toil(30, "p1") if r["entry_id"] == "m9"]

    def test_somebody_elses_hand_work_is_not_our_backlog(self, ledger):
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m10','memory','a','p1',
                   'Multi-city subscribers manually reconstructed the geography by hand.',1.0)"""
            )
        assert not [r for r in facts.toil(30, "p1") if r["entry_id"] == "m10"]

    def test_deliberate_manual_strategy_is_not_toil(self, ledger):
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m11','memory','a','p1',
                   'Core playbook: charge before building, sell manually first, automate later.',1.0)"""
            )
        assert not [r for r in facts.toil(30, "p1") if r["entry_id"] == "m11"]

    def test_themes_match_inflected_words(self, ledger):
        # \bsync\b never matched "synced" and \bmigration\b never matched
        # "migrations", so themed work was landing in the "other" bucket.
        facts, db = ledger
        with db:
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m12','memory','a','p1',
                   'Those changes must be manually synced into the recall bundle.',1.0)"""
            )
            db.execute(
                """INSERT INTO memory (id, type, agent_id, project, content, confidence)
                   VALUES ('m13','memory','a','p1',
                   'Heavy migrations must be executed manually as a separate task.',1.0)"""
            )
        theme = {r["entry_id"]: r["theme"] for r in facts.toil(30, "p1")}
        assert theme["m12"] == "cross-copy in sync"
        assert theme["m13"] == "deploy / migrate"
