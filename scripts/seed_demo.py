import argparse
import json
import pathlib
import random
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from artel.store.schema import SCHEMA  # noqa: E402

SEED = 20260916
PROJECTS = ("harbor-api", "harbor-web", "harbor-etl", "harbor-infra")
AGENTS = ("dock-1", "dock-2", "crane", "tug", "pilot")

# Every theme the miner knows, so the panel shows its full range rather than six
# variations on "deploy". Written the way a tired agent actually writes a note.
TOIL_NOTES = (
    (
        "harbor-infra",
        "Production deploys do not run migrations automatically. After each release "
        "someone has to apply them manually with `alembic upgrade head` against the "
        "writer instance, and the release is silently half-live until they do.",
    ),
    (
        "harbor-web",
        "The pricing table in `marketing/plans.html` must be manually kept in sync "
        "with `billing/plans.py`. They have drifted twice this quarter and both times "
        "a customer found it before we did.",
    ),
    (
        "harbor-etl",
        "The carrier rate cache has no scheduler attached. Someone re-runs "
        "`scripts/refresh_rates.py` by hand most Mondays; when they forget to, quotes "
        "go out against the previous week's rates.",
    ),
    (
        "harbor-etl",
        "The ingest lag alarm computes against a baseline that is never reset, so it "
        "false-alarms after every backfill until the baseline is manually cleared in "
        "the database.",
    ),
    (
        "harbor-api",
        "The carrier portal 2FA token expires every 30 days and has to be re-entered "
        "manually before the nightly scrape. Nothing warns us; we find out from the "
        "empty-result alert the next morning.",
    ),
    (
        "harbor-infra",
        "New shipper accounts need their S3 share and read-only view created manually. "
        "The CDK stack does not discover new accounts, so each one is a ticket.",
    ),
    (
        "harbor-web",
        "French shipping-label copy is translated by hand each time the English "
        "changes. Nothing flags the drift, so the French page has been a release "
        "behind since June.",
    ),
    (
        "harbor-api",
        "Failed Stripe renewals have to be manually reactivated once support confirms "
        "the payment cleared. The webhook marks them inactive and nothing walks it "
        "back.",
    ),
    (
        "harbor-api",
        "The rate-quote worker wedges under burst load and somebody restarts it by "
        "hand. It has never recovered on its own.",
    ),
    (
        "harbor-infra",
        "Release notes are assembled manually from `git log` before each tag, which is "
        "why the last three tags have none.",
    ),
    (
        "harbor-etl",
        "Schema changes at the carrier end are discovered manually, usually by seeing "
        "a column go null in a dashboard, not by an alert.",
    ),
    (
        "harbor-web",
        "The sitemap is not auto-managed by the generator; new route URLs must be "
        "spliced in manually after each build completes.",
    ),
)

# Notes that mention manual work or repetition and are NOT toil. They exist so the
# seeded panel demonstrates the filter doing its job, not just the happy path.
DECOY_NOTES = (
    (
        "harbor-infra",
        "`scripts/coverage --check` verifies the manifest on every push; do not "
        "manually edit `coverage.json`, it is generated.",
    ),
    (
        "harbor-api",
        "The search index is kept in sync at every write path including the bulk "
        "importer, with a reconcile pass on startup.",
    ),
    (
        "harbor-web",
        "Playbook we settled on: charge before building, sell manually first, automate "
        "only after the first paying customer.",
    ),
    (
        "harbor-etl",
        "In backtests across freight lanes, the equal-weight basket won every time; "
        "the weighting barely mattered.",
    ),
)

FACTS = (
    (
        "harbor-api",
        "Rate quotes above 40ft are rounded up to the next container class before pricing.",
    ),
    (
        "harbor-api",
        "The `/quotes` endpoint returns 202 and a poll token when a carrier is slow; clients must not treat it as failure.",
    ),
    (
        "harbor-etl",
        "Carrier CSVs arrive in local time with no offset. We normalise to UTC on ingest using the carrier's registered region.",
    ),
    (
        "harbor-etl",
        "Duplicate shipment rows are legitimate: a leg can be re-quoted. Dedupe on (shipment_id, leg, quoted_at), never on shipment_id alone.",
    ),
    (
        "harbor-web",
        "The booking funnel drops 60% at the customs-declaration step; that step is the product's real conversion problem.",
    ),
    (
        "harbor-infra",
        "The writer instance is in ca-central-1 and readers are regional; cross-region writes silently queue rather than error.",
    ),
)

DECISIONS = (
    (
        "harbor-api",
        "Return 202 with a poll token for slow carriers instead of holding the connection.",
        "Three carriers take longer than the 30s gateway timeout under load. Holding the connection turned a slow quote into a failed one and clients retried, doubling the load on the carrier that was already slow.",
        [
            "Raise the gateway timeout to 120s",
            "Queue everything and make quotes fully async",
            "Drop the slow carriers",
        ],
    ),
    (
        "harbor-etl",
        "Dedupe shipments on (shipment_id, leg, quoted_at) rather than shipment_id.",
        "A leg can legitimately be re-quoted, so shipment_id is not unique and deduping on it silently dropped the newer price. The bug surfaced as customers seeing stale quotes, which read as a caching problem for two weeks.",
        ["Dedupe on shipment_id and keep the newest", "Add a surrogate key upstream"],
    ),
    (
        "harbor-infra",
        "Pin the writer to ca-central-1 and route readers regionally.",
        "Cross-region writes were queueing rather than erroring, so a partition looked like latency. One writer makes the failure mode loud.",
        [
            "Multi-region writes with conflict resolution",
            "Region-local writers with nightly reconciliation",
        ],
    ),
    (
        "harbor-web",
        "Rebuild the customs-declaration step before touching the rest of the funnel.",
        "60% of drop-off is concentrated in that one step. Work anywhere else in the funnel is optimising traffic that has already left.",
        ["A/B test the whole funnel", "Add a progress indicator", "Offer a save-and-return link"],
    ),
    (
        "harbor-api",
        "Cache carrier rates for 15 minutes rather than per-request.",
        "Carriers rate-limit at 60 req/min and we were burning the budget on identical lookups. Rates move slower than the cache window, so staleness costs less than the throttling did.",
        ["No cache", "Cache for 24h", "Per-carrier adaptive TTL"],
    ),
    (
        "harbor-etl",
        "Treat carrier schema drift as a data incident, not a bug.",
        "Carriers change columns without notice and the fix is always the same shape. Naming it an incident gets it a runbook and an owner instead of a surprise each time.",
        ["Contract tests against carrier feeds", "Pin to a schema version"],
    ),
    (
        "harbor-infra",
        "Keep SQLite with WAL rather than moving to Postgres.",
        "Peak write volume is under 40/s and the operational cost of a managed Postgres is the dominant expense at this size. Revisit above 500/s sustained.",
        ["Managed Postgres", "Aurora Serverless", "DynamoDB"],
    ),
    (
        "harbor-web",
        "Ship the French labels behind a flag rather than blocking release on translation.",
        "Translation lags the English by a release and blocking made both late. A flag lets English ship on time and French land when it is ready.",
        ["Block release until translated", "Machine-translate and review after"],
    ),
)

# Public list prices per million tokens, used to price fictional usage so the demo
# shows real arithmetic instead of $0.00. The rates a live instance uses are fetched
# from OpenRouter at runtime; these exist only so the static export has a number.
_PER_M = {
    "claude-opus-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-5": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 0.10, 1.25),
}
DEMO_RATES = {
    model: {
        "input": i / 1e6,
        "output": o / 1e6,
        "cache_read": cr / 1e6,
        "cache_write": cw / 1e6,
    }
    for model, (i, o, cr, cw) in _PER_M.items()
}

# Two billing modes on purpose. A fleet that runs some work on a seat and some on
# the API is the normal case, and it is the only way the demo can show the
# invoice/equivalent split that the ledger exists to keep apart.
MODELS = (
    ("claude-opus-5", "metered", 0.40),
    ("claude-sonnet-5", "metered", 0.42),
    ("claude-haiku-4-5-20251001", "metered", 0.18),
    ("claude-opus-5", "subscription", 0.40),
    ("claude-sonnet-5", "subscription", 0.42),
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build(path: pathlib.Path, days: int = 14) -> dict:
    rnd = random.Random(SEED)
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    now = datetime.now(UTC)

    for agent in AGENTS:
        db.execute(
            "INSERT INTO agents (id, api_key, created_at) VALUES (?,?,?)",
            (agent, f"demo-key-{agent}", _iso(now - timedelta(days=days + 7))),
        )

    sessions: list[tuple[str, str, datetime]] = []
    for i in range(22):
        age = rnd.uniform(0.2, days - 0.5)
        started = now - timedelta(days=age)
        sessions.append(
            (str(uuid.UUID(int=rnd.getrandbits(128))), PROJECTS[i % len(PROJECTS)], started)
        )

    for sid, project, started in sessions:
        model, mode, weight = MODELS[rnd.randrange(len(MODELS))]
        turns = rnd.randint(6, 90)
        minutes = rnd.randint(12, 180)
        ended = started + timedelta(minutes=minutes)
        db.execute(
            """INSERT INTO usage_events
               (id, agent_id, project, session_id, model, billing_mode, turns,
                input_tokens, output_tokens, cache_read, cache_write,
                window_start, window_end, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                AGENTS[rnd.randrange(len(AGENTS))],
                project,
                sid,
                model,
                mode,
                turns,
                rnd.randint(40, 900),
                int(turns * rnd.uniform(700, 2400) * weight),
                int(turns * rnd.uniform(18000, 60000)),
                int(turns * rnd.uniform(3000, 14000)),
                _iso(started),
                _iso(ended),
                _iso(ended),
            ),
        )

    for i, (project, decision, rationale, alts) in enumerate(DECISIONS):
        candidates = [s for s in sessions if s[1] == project] or sessions
        sid, _, started = candidates[i % len(candidates)]
        db.execute(
            """INSERT INTO decisions
               (id, project, agent_id, decision, rationale, alternatives, created_at, session_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                project,
                AGENTS[i % len(AGENTS)],
                decision,
                rationale,
                json.dumps(alts),
                _iso(started + timedelta(minutes=rnd.randint(20, 120))),
                sid,
            ),
        )

    notes = (
        [(p, c, "toil") for p, c in TOIL_NOTES]
        + [(p, c, "decoy") for p, c in DECOY_NOTES]
        + [(p, c, "fact") for p, c in FACTS]
    )
    for i, (project, content, kind) in enumerate(notes):
        db.execute(
            """INSERT INTO memory
               (id, type, agent_id, project, content, confidence, tags, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                "memory",
                AGENTS[i % len(AGENTS)],
                project,
                content,
                round(rnd.uniform(0.62, 0.98), 2),
                json.dumps([kind, project]),
                _iso(now - timedelta(days=rnd.uniform(0.5, days - 1))),
                _iso(now - timedelta(days=rnd.uniform(0.1, 0.5))),
            ),
        )

    db.commit()
    counts = {
        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("usage_events", "decisions", "memory", "agents")
    }
    db.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/sandbox/demo.db")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = build(out, args.days)
    print(f"seeded {out}")
    for t, n in counts.items():
        print(f"  {n:>4}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
