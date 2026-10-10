#!/usr/bin/env python3
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "artel/server/static/index.html"
SITE = ROOT / "web/index.html"
CSS_START, CSS_END = "/*themes:start*/", "/*themes:end*/"
JS_START, JS_END = "/*themes-list:start*/", "/*themes-list:end*/"
DEFAULT_THEME = "monokai"
DIM_CONTRAST = 6.0
FAINT_CONTRAST = 4.5
ACCENT_CONTRAST = 3.0


def rgb(value):
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def luminance(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def legible(color, bg, fg, target):
    c, b, f = rgb(color), rgb(bg), rgb(fg)
    for step in range(21):
        mixed = tuple(round(c[i] + (f[i] - c[i]) * step / 20) for i in range(3))
        if contrast(mixed, b) >= target:
            break
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def tokens(body):
    return {k: v.strip() for k, v in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", body)}


def site_tokens(v):
    bg, fg = v["bg"], v["t1"]
    manual = v["red"] if v["orange"].lower() == v["accent"].lower() else v["orange"]
    scheme = "light" if luminance(rgb(bg)) > 0.4 else "dark"
    return (
        f"--bg:{bg};--panel:{v['s1']};--row:{v['s2']};--fg:{fg};"
        f"--dim:{legible(v['t3'], bg, fg, DIM_CONTRAST)};"
        f"--faint:{legible(v['t4'], bg, fg, FAINT_CONTRAST)};"
        f"--rule:{v['b1']};--edge:{v['b2']};"
        f"--money:{legible(v['accent'], bg, fg, ACCENT_CONTRAST)};"
        f"--manual:{legible(manual, bg, fg, ACCENT_CONTRAST)};"
        f"--auto:{legible(v['green'], bg, fg, ACCENT_CONTRAST)};color-scheme:{scheme}"
    )


def between(text, start, end, replacement):
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{start}{replacement}{end}{tail}"


def build():
    dash = DASHBOARD.read_text()
    blocks = {"": tokens(re.search(r":root\{(.*?)\n\}", dash, re.S).group(1))}
    for name, body in re.findall(r'\[data-theme="([^"]+)"\]\{(.*?)\n\}', dash, re.S):
        blocks[name] = tokens(body)
    listing = re.search(r"const _THEMES=\[(.*?)\];", dash, re.S).group(1)
    themes = re.findall(
        r"id:'([^']+)',\s*label:'([^']+)',\s*dark:'([^']*)',\s*light:'([^']+)',"
        r"\s*bd:'([^']+)',ad:'([^']+)',bl:'([^']+)',\s*al:'([^']+)'",
        listing,
    )
    css, rows = [], []
    for tid, label, dark, light, bd, ad, bl, al in themes:
        for mode, key in (("dark", dark), ("light", light)):
            selector = f':root[data-theme="{tid}-{mode}"]'
            if tid == DEFAULT_THEME and mode == "dark":
                selector = ":root"
            css.append(f"{selector}{{{site_tokens(blocks[key])}}}")
        rows.append(f'["{tid}","{label}","{bd}","{ad}","{bl}","{al}"]')
    page = SITE.read_text()
    page = between(page, CSS_START, CSS_END, "\n" + "\n".join(css) + "\n")
    page = between(page, JS_START, JS_END, "[" + ",".join(rows) + "]")
    return page


def main():
    page = build()
    if "--check" in sys.argv:
        if page != SITE.read_text():
            print("web/index.html themes are stale: run scripts/gen_site_themes.py")
            return 1
        return 0
    SITE.write_text(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
