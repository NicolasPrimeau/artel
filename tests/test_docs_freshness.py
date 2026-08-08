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
