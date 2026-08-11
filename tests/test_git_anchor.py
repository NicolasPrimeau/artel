import subprocess

import pytest

from artel.server import git_anchor

SOURCE_V1 = '''"""module docstring."""


def role_of(agent_id):
    return "agent"


def untouched(x):
    return x + 1
'''

SOURCE_V2 = '''"""module docstring."""


def role_of(agent_id):
    if agent_id == "archivist":
        return "archivist"
    return "agent"


def untouched(x):
    return x + 1
'''

SOURCE_V3_OTHER_FN = '''"""module docstring."""


def role_of(agent_id):
    return "agent"


def untouched(x):
    return x + 2
'''


def _commit(repo_dir, content, message):
    (repo_dir / "auth.py").write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", message],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    _commit(tmp_path, SOURCE_V1, "initial")
    return tmp_path


def test_reads_a_file_from_head(repo):
    assert "def role_of" in git_anchor.read_blob(str(repo), "auth.py")


def test_missing_path_reads_as_nothing(repo):
    assert git_anchor.read_blob(str(repo), "nope.py") is None


def test_file_and_symbol_anchors_differ(repo):
    whole = git_anchor.anchor_sha(str(repo), "auth.py")
    symbol = git_anchor.anchor_sha(str(repo), "auth.py::role_of")
    assert whole and symbol and whole != symbol


def test_editing_another_function_leaves_the_symbol_anchor_alone(repo):
    """Precision is the point: a file-level anchor would fire on any edit."""
    before = git_anchor.anchor_sha(str(repo), "auth.py::role_of")
    _commit(repo, SOURCE_V3_OTHER_FN, "touch a different function")
    assert git_anchor.anchor_sha(str(repo), "auth.py::role_of") == before
    assert git_anchor.anchor_sha(str(repo), "auth.py::untouched") != before


def test_changed_passes_only_after_the_symbol_actually_moves(repo):
    baseline = git_anchor.anchor_sha(str(repo), "auth.py::role_of")

    ok, reason = git_anchor.evaluate(str(repo), "auth.py::role_of", "changed", None, baseline)
    assert not ok and "unchanged" in reason

    _commit(repo, SOURCE_V2, "actually fix role_of")
    ok, reason = git_anchor.evaluate(str(repo), "auth.py::role_of", "changed", None, baseline)
    assert ok, reason


def test_changed_without_a_baseline_refuses(repo):
    """An unfalsifiable check must fail, not pass."""
    ok, reason = git_anchor.evaluate(str(repo), "auth.py::role_of", "changed", None, None)
    assert not ok
    assert "no baseline" in reason


def test_contains_reads_the_repository(repo):
    ok, _ = git_anchor.evaluate(str(repo), "auth.py", "contains", "def role_of", None)
    assert ok
    ok, reason = git_anchor.evaluate(str(repo), "auth.py", "contains", "def absent", None)
    assert not ok and "does not contain" in reason


def test_exists(repo):
    assert git_anchor.evaluate(str(repo), "auth.py", "exists", None, None)[0]
    assert not git_anchor.evaluate(str(repo), "ghost.py", "exists", None, None)[0]


def test_unknown_expectation_is_refused(repo):
    ok, reason = git_anchor.evaluate(str(repo), "auth.py", "vibes", None, None)
    assert not ok and "unknown git expectation" in reason


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../etc/passwd", "a/../../b"])
def test_paths_cannot_escape_the_configured_root(repo, bad):
    with pytest.raises(git_anchor.GitAnchorError):
        git_anchor.split_anchor(bad)


def test_unconfigured_root_is_a_clear_error():
    with pytest.raises(git_anchor.GitAnchorError, match="no repository configured"):
        git_anchor.read_blob("", "auth.py")


def test_non_repository_is_a_clear_error(tmp_path):
    with pytest.raises(git_anchor.GitAnchorError, match="not a git repository"):
        git_anchor.read_blob(str(tmp_path), "auth.py")
