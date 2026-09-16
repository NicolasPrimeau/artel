import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "web" / "docs"

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
    redirects = []
    for page in pages:
        redirects.append(
            {"source": f"/{page}/", "destination": f"/docs/{page}/", "permanent": True}
        )
        redirects.append({"source": f"/{page}", "destination": f"/docs/{page}/", "permanent": True})
    redirects.append(
        {"source": "/reference/", "destination": "/docs/reference/rest/", "permanent": True}
    )
    cfg = dict(BASE)
    cfg["redirects"] = redirects
    cfg["headers"] = [
        {
            "source": "/sandbox/data/(.*)",
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
