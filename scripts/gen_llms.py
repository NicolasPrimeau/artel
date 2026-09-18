import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
WEB = ROOT / "web"
BASE = "https://artel.run"

# The order a reader should meet the pages in, which is the order mkdocs.yml navigates
# them. Anything not listed here is appended under Reference.
SECTIONS = {
    "Guides": ["plugin", "capture", "archivist", "compile-mode", "blueprints", "decisions", "mesh"],
    "Operating it": ["dashboard", "ledger", "usage", "auth", "overhead", "adaptive-control"],
    "Reference": ["clients", "spec", "directive_spec", "architecture", "documentation"],
}
FULL_SKIP = {"plan", "reddit-posts"}


def _front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.index("\n---", 4)
    meta = dict(
        (m.group(1), m.group(2).strip().strip("\"'"))
        for m in re.finditer(r"^([a-z_]+): (.+)$", text[4:end], re.M)
    )
    return meta, text[end + 4 :].lstrip("\n")


def _title(name: str, pages: dict) -> str:
    meta, body = pages.get(name, ({}, ""))
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    return meta.get("title") or (heading.group(1).strip() if heading else name)


def main() -> int:
    if not (WEB / "index.html").exists():
        print("web/index.html missing", file=sys.stderr)
        return 1
    pages = {p.stem: _front_matter(p.read_text()) for p in sorted(DOCS.glob("*.md"))}
    listed = {name for names in SECTIONS.values() for name in names}
    missing = [n for n in pages if n not in listed and n not in FULL_SKIP and n != "index"]
    if missing:
        SECTIONS["Reference"].extend(sorted(missing))

    out = [
        "# Artel",
        "",
        "> A self-hosted shared notepad for you and every AI agent you run. Agents write down what"
        " they learn and get it back when it matters, and the ledger prices the work: what each"
        " session and each decision cost, and which chores the fleet keeps doing by hand."
        " Any client that speaks HTTP or MCP can join. MIT licensed.",
        "",
        f"- Site: {BASE}/",
        f"- Docs: {BASE}/docs/",
        f"- Live ledger, real anonymized figures: {BASE}/ledger/",
        "- Source: https://github.com/NicolasPrimeau/artel",
        "",
    ]
    for section, names in SECTIONS.items():
        out.append(f"## {section}")
        out.append("")
        for name in names:
            meta, _ = pages.get(name, ({}, ""))
            desc = meta.get("description", "")
            title = _title(name, pages)
            out.append(f"- [{title}]({BASE}/docs/{name}/)" + (f": {desc}" if desc else ""))
        out.append("")
    out += [
        "## Reference, generated from the code",
        "",
        f"- [REST API]({BASE}/docs/reference/rest/): every endpoint, from the OpenAPI schema.",
        f"- [MCP tools]({BASE}/docs/reference/mcp-tools/): every tool an agent can call.",
        f"- [Configuration]({BASE}/docs/reference/configuration/): every setting.",
        "",
        "## Optional",
        "",
        f"- [Everything as one file]({BASE}/llms-full.txt): the whole documentation set.",
        f"- [Plan]({BASE}/docs/plan/): what is built and what is next.",
        "",
    ]
    (WEB / "llms.txt").write_text("\n".join(out))

    full = [
        "# Artel, full documentation",
        "",
        f"Generated from the docs at {BASE}/docs/. Each section names the page it came from.",
        "",
    ]
    for name, (meta, body) in pages.items():
        if name in FULL_SKIP:
            continue
        full += ["---", "", f"# Source: {BASE}/docs/{name}/", ""]
        if meta.get("description"):
            full += [f"> {meta['description']}", ""]
        full += [re.sub(r"<!--.*?-->\n?", "", body, flags=re.S).strip(), ""]
    (WEB / "llms-full.txt").write_text("\n".join(full))

    print(
        f"llms.txt: {sum(len(v) for v in SECTIONS.values())} pages; "
        f"llms-full.txt: {len((WEB / 'llms-full.txt').read_text()) // 1024} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
