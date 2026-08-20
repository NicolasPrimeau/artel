import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
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


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _FakeClient:
    def __init__(self, open_tasks, calls):
        self._open_tasks = open_tasks
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        return _FakeResponse(self._open_tasks)

    def post(self, path, json=None):
        self.calls.append(("POST", path))
        return _FakeResponse({"id": "new-task-id"}, 201)


def _run_open_task(monkeypatch, open_tasks):
    import httpx

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(open_tasks, calls), raising=True)
    monkeypatch.setenv("ARTEL_URL", "http://artel.test")
    monkeypatch.setenv("ARTEL_AGENT_ID", "tester")
    monkeypatch.setenv("ARTEL_API_KEY", "k")
    stale = [{"page": "docs/auth.md", "anchor": "a.py::b", "status": "stale"}]
    assert check_docs.open_task(stale) == 0
    return calls


def test_files_a_task_when_none_is_open(monkeypatch):
    calls = _run_open_task(monkeypatch, [])
    assert ("POST", "/tasks") in calls


def test_comments_instead_of_filing_a_duplicate(monkeypatch):
    """A check that runs every commit must not breed one task per commit."""
    calls = _run_open_task(monkeypatch, [{"id": "existing-task"}])
    assert ("POST", "/tasks") not in calls
    assert ("POST", "/tasks/existing-task/comments") in calls


def test_no_credentials_is_a_clean_failure(monkeypatch):
    for var in ("ARTEL_URL", "ARTEL_AGENT_ID", "ARTEL_API_KEY", "ARTEL_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert check_docs.open_task([{"page": "p", "anchor": "a", "status": "stale"}]) == 1


class TestCoverage:
    def test_surfaces_are_read_from_the_code(self):
        surfaces = check_docs._surfaces()
        # Route modules name the surface; tool groups fold into them, so a primitive
        # served over both transports is one entry rather than two.
        assert surfaces["blueprints"] == "REST routes + MCP tools"
        assert surfaces["graph"] == "MCP tools"
        assert "memorys" not in surfaces

    def test_every_shipped_surface_is_claimed_by_a_page(self):
        uncovered, _ = check_docs.coverage()
        assert uncovered == [], (
            "these ship in the code with no prose describing them: " + "; ".join(uncovered)
        )

    def test_no_claim_outlives_its_code(self):
        _, orphaned = check_docs.coverage()
        assert orphaned == [], "; ".join(orphaned)

    def test_an_unclaimed_surface_is_reported(self, monkeypatch):
        # The gate this replaces could not fail on absence — anchors only compare
        # prose that already exists. Prove this one does.
        monkeypatch.setattr(
            check_docs, "_surfaces", lambda: {"warpdrive": "REST routes + MCP tools"}
        )
        uncovered, _ = check_docs.coverage()
        assert any("warpdrive" in row for row in uncovered)

    def test_reference_pages_do_not_satisfy_coverage(self, monkeypatch):
        # docs/reference/ is generated from the source and mentions everything, so
        # counting it would make the check vacuously green.
        claimed = check_docs._claims()
        for pages in claimed.values():
            assert not any("reference/" in page for page in pages)


class TestSiteBuilds:
    def test_site_builds_in_strict_mode(self):
        # CI builds with --strict, where a broken link or a missing image is an error
        # rather than a warning. Building without it locally shipped a docs deploy
        # that failed on image paths left pointing at the README's directory.
        #
        # No -q. Strict mode counts warnings as they are EMITTED, so quieting them
        # makes the count zero and the build exits 0 while still printing "Aborted
        # with 1 warnings in strict mode!". A quiet strict build is a false green.
        import shutil
        import subprocess
        import tempfile

        import pytest

        if shutil.which("mkdocs") is None:
            pytest.skip("mkdocs not installed")
        # docs/reference/ is generated by gen_docs.py and gitignored. In a clean
        # checkout the nav points at pages that do not exist yet, so a strict build
        # fails for a reason that has nothing to do with the prose. CI's docs job
        # generates them before building; this guards local runs, where the
        # temptation is to reach for -q and get a false green instead.
        if not (DOCS / "reference" / "rest.md").exists():
            pytest.skip("run scripts/gen_docs.py first; reference pages are generated")
        with tempfile.TemporaryDirectory() as out:
            proc = subprocess.run(
                ["mkdocs", "build", "--strict", "-d", out],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        assert proc.returncode == 0, proc.stderr[-2000:]
