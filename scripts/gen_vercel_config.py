import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DOCS = WEB / "docs"

BASE = {
    "$schema": "https://openapi.vercel.sh/vercel.json",
    "framework": None,
    "buildCommand": None,
    "outputDirectory": "web",
    "trailingSlash": True,
}


def main() -> int:
    if not DOCS.is_dir():
        print("web/docs not built; run mkdocs -f mkdocs.web.yml first", file=sys.stderr)
        return 1
    # Every page GitHub Pages served at the root now lives under /docs. Without these
    # the old indexed URLs 404 the moment the domain moves.
    pages = sorted(
        str(p.parent.relative_to(DOCS)).replace("\\", "/")
        for p in DOCS.rglob("index.html")
        if p.parent != DOCS
    )
    # Sources carry the trailing slash: with trailingSlash enabled Vercel 308s
    # /plugin -> /plugin/ BEFORE redirects are evaluated, so a slash-less source
    # never matches. Both forms are emitted so the slash-less URL is a single hop.
    # A redirect whose source is also a real top-level path would shadow it: the
    # docs contain a page called "ledger", and /ledger/ is the ledger demo. Vercel
    # evaluates redirects before static files, so the demo simply vanished.
    reserved = {d.name for d in WEB.iterdir() if d.is_dir() and d.name != "docs"}
    redirects = []
    for page in pages:
        if page.split("/")[0] in reserved:
            print(f"  skipping /{page} -- shadows the real /{page}")
            continue
        redirects.append(
            {"source": f"/{page}/", "destination": f"/docs/{page}/", "permanent": True}
        )
        redirects.append({"source": f"/{page}", "destination": f"/docs/{page}/", "permanent": True})
    redirects.append(
        {"source": "/reference/", "destination": "/docs/reference/rest/", "permanent": True}
    )
    # /sandbox was this page's name for one afternoon. It promised the Artel
    # dashboard and delivered the ledger, so the ledger took the honest name and
    # /sandbox is left free for a live instance later.
    redirects.append({"source": "/sandbox/", "destination": "/ledger/", "permanent": False})
    redirects.append({"source": "/sandbox", "destination": "/ledger/", "permanent": False})
    # One host serves the site. Without this, www.artel.run answers everything the
    # apex does and the two compete for the same pages in search results.
    redirects.append(
        {
            "source": "/(.*)",
            "has": [{"type": "host", "value": "www.artel.run"}],
            "destination": "https://artel.run/$1",
            "permanent": True,
        }
    )
    cfg = dict(BASE)
    cfg["redirects"] = redirects
    cfg["headers"] = [
        {
            "source": "/ledger/data/(.*)",
            "headers": [{"key": "Cache-Control", "value": "public, max-age=300"}],
        }
    ]
    (ROOT / "vercel.json").write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"vercel.json: {len(redirects)} redirects from the old flat URLs")
    for r in redirects[:6]:
        print(f"  {r['source']} -> {r['destination']}")
    print(f"  ... and {len(redirects) - 6} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
