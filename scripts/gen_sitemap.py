import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BASE = "https://artel.run"

# mkdocs writes its own sitemap covering /docs only. Search Console needs one that
# also knows about the landing page and the sandbox, or they are discoverable by
# crawl alone.
PRIORITY = {"": "1.0", "ledger/": "0.8"}


def main() -> int:
    if not (WEB / "index.html").exists():
        print("web/index.html missing", file=sys.stderr)
        return 1
    today = datetime.date.today().isoformat()
    urls = [""]
    if (WEB / "ledger" / "index.html").exists():
        urls.append("ledger/")
    docs = WEB / "docs"
    if docs.is_dir():
        urls.append("docs/")
        urls += sorted(
            f"docs/{p.parent.relative_to(docs).as_posix()}/"
            for p in docs.rglob("index.html")
            if p.parent != docs
        )
    body = "\n".join(
        f"  <url><loc>{BASE}/{u}</loc><lastmod>{today}</lastmod>"
        f"<priority>{PRIORITY.get(u, '0.6')}</priority></url>"
        for u in urls
    )
    (WEB / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "\n</urlset>\n"
    )
    (WEB / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")
    print(f"sitemap.xml: {len(urls)} urls; robots.txt written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
