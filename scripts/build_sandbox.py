import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import seed_demo  # noqa: E402

DAY_RANGES = (7, 30, 90)
TOIL_DAYS = 90

# The page talks to /api/*. Rather than edit it -- and risk the static copy drifting
# from the app -- this shim answers those calls from the bundles. page.html ships
# byte-identical to what the server serves.
# Analytics belong to the public sandbox only. page.html is served by every
# self-hosted instance, so a tag committed there would have other people's
# dashboards reporting to this property.
GA = """<script async src="https://www.googletagmanager.com/gtag/js?id=G-CYMPQ0NXJN"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments)}
gtag("js", new Date());
gtag("config", "G-CYMPQ0NXJN");
</script>
"""

SHIM = """<script>
(function () {
  const real = window.fetch.bind(window);
  const cache = {};
  const bundle = async (days) => {
    const key = [7, 30, 90].includes(+days) ? +days : 90;
    if (!cache[key]) cache[key] = real(`data/d${key}.json`).then((r) => r.json());
    return cache[key];
  };
  const ok = (body) => new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.includes("/api/")) return real(input, init);
    const days = url.searchParams.get("days") || "30";
    const project = url.searchParams.get("project");
    const b = await bundle(days);
    const name = url.pathname.split("/api/")[1].split("/")[0];
    if (name === "toil") {
      const t = b.toil;
      if (!project) return ok(t);
      return ok({
        days: t.days,
        themes: t.themes.filter((x) => (x.projects || []).includes(project)),
        rows: t.rows.filter((r) => r.project === project),
      });
    }
    return ok(b[name] || { rows: [] });
  };
})();
</script>
"""


def _payloads(days: int) -> dict:
    from artel.ledger import facts

    return {
        "projects": {"days": days, "rows": facts.by_project(days), "totals": facts.totals(days)},
        "sessions": {"days": days, "rows": facts.by_session(days, 60)},
        "decisions": {"days": days, "rows": facts.by_decision(days, 60)},
        "toil": {
            "days": TOIL_DAYS,
            "themes": facts.toil_themes(TOIL_DAYS, None),
            "rows": facts.toil(TOIL_DAYS, None, 25),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/sandbox")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)

    tmp = pathlib.Path(tempfile.mkdtemp()) / "demo.db"
    counts = seed_demo.build(tmp, args.days)

    os.environ["DB_PATH"] = str(tmp)
    os.environ["MODEL_RATES"] = json.dumps(seed_demo.DEMO_RATES)

    totals = None
    for days in DAY_RANGES:
        payload = _payloads(days)
        (data / f"d{days}.json").write_text(json.dumps(payload, separators=(",", ":")))
        if days == 30:
            totals = payload["projects"]["totals"]

    page = (pathlib.Path(__file__).resolve().parents[1] / "artel/ledger/page.html").read_text()
    marker = "<script>"
    if marker not in page:
        print("page.html has no <script> to anchor the shim before", file=sys.stderr)
        return 1
    at = page.index(marker)
    (out / "index.html").write_text(page[:at] + GA + SHIM + page[at:])

    shutil.rmtree(tmp.parent, ignore_errors=True)

    print(f"sandbox -> {out}")
    for t, n in counts.items():
        print(f"  {n:>4}  {t}")
    if totals:
        print(f"  30d: ${totals['billed']:,.2f} billed, {totals['sessions']} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
