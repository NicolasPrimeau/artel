import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "gen_site_themes.py"


def _module():
    spec = importlib.util.spec_from_file_location("gen_site_themes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_landing_page_themes_match_dashboard():
    module = _module()
    assert module.build() == module.SITE.read_text()


def test_every_theme_has_dark_and_light():
    module = _module()
    page = module.build()
    assert page.count(':root[data-theme="') == 31
    assert "color-scheme:light" in page and "color-scheme:dark" in page
