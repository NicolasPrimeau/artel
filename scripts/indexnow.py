import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
HOST = "artel.run"
ENDPOINT = "https://api.indexnow.org/indexnow"


def main() -> int:
    keys = [p for p in WEB.glob("*.txt") if re.fullmatch(r"[0-9a-f]{32}", p.stem)]
    if len(keys) != 1:
        print(f"expected one IndexNow key file in web/, found {len(keys)}", file=sys.stderr)
        return 1
    key = keys[0].stem
    urls = re.findall(r"<loc>([^<]+)</loc>", (WEB / "sitemap.xml").read_text())
    if not urls:
        print("sitemap.xml lists no urls", file=sys.stderr)
        return 1
    body = json.dumps(
        {
            "host": HOST,
            "key": key,
            "keyLocation": f"https://{HOST}/{key}.txt",
            "urlList": urls,
        }
    ).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow {r.status}: submitted {len(urls)} urls")
    except urllib.error.HTTPError as e:
        print(f"IndexNow rejected the submission: {e.code} {e.read()[:200]!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
