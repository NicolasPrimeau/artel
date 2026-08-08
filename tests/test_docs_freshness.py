import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "docs" / ".anchors.lock"


def _module():
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check_docs = _module()


def test_front_matter_parses_anchor_lists():
    meta = check_docs._front_matter("---\nanchors:\n  - a/b.py\n  - a/b.py::sym\n---\n\n# Page\n")
    assert meta["anchors"] == ["a/b.py", "a/b.py::sym"]


def test_page_without_front_matter_has_no_anchors():
    assert check_docs._front_matter("# Just a page\n") == {}


def test_module_anchor_resolves():
    assert check_docs._anchor_sha("artel/server/auth.py") is not None


def test_symbol_anchor_resolves():
    assert check_docs._anchor_sha("artel/server/auth.py::role_of") is not None


def test_missing_file_and_symbol_resolve_to_nothing():
    assert check_docs._anchor_sha("artel/does/not/exist.py") is None
    assert check_docs._anchor_sha("artel/server/auth.py::no_such_symbol") is None


def test_symbol_and_module_anchors_are_distinct():
    module = check_docs._anchor_sha("artel/server/auth.py")
    symbol = check_docs._anchor_sha("artel/server/auth.py::role_of")
    assert module != symbol


@pytest.mark.skipif(not LOCK.exists(), reason="no anchor lockfile")
def test_every_committed_anchor_is_fresh():
    """The docs in this commit describe the code in this commit.

    A failure here means a prose page's subject changed: correct the page, then
    run `scripts/check_docs.py --update`.
    """
    stale = [r for r in check_docs.collect() if r["status"] != check_docs.FRESH]
    assert not stale, "\n".join(f"{r['status']}: {r['page']} -> {r['anchor']}" for r in stale)


@pytest.mark.skipif(not LOCK.exists(), reason="no anchor lockfile")
def test_lockfile_has_no_orphans():
    """Every recorded anchor is still referenced by a page, and still resolves."""
    lock = json.loads(LOCK.read_text())
    referenced = {r["anchor"] for r in check_docs.collect()}
    assert set(lock) <= referenced, f"orphaned: {set(lock) - referenced}"
    unresolvable = [a for a in lock if check_docs._anchor_sha(a) is None]
    assert not unresolvable, f"anchors no longer in the codebase: {unresolvable}"
