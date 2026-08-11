"""Read build state out of a git repository, for done-checks that cannot be faked.

A completion contract checks what the agent SAYS. This reads what the agent DID.
An agent can return a perfectly-shaped payload claiming it fixed a function; it
cannot fake the contents of HEAD.

Anchors use the same syntax as the documentation freshness checker:

    artel/server/auth.py            the whole file
    artel/server/auth.py::role_of   one function or class, by span hash

Paths index into the git object database via GitPython — they are never passed to
a subprocess, so a model-authored path cannot become a git argument. The repository
root comes from configuration only, never from the blueprint.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from ..compile.anchors import compile_source

EXPECT_EXISTS = "exists"
EXPECT_CONTAINS = "contains"
EXPECT_CHANGED = "changed"
EXPECTATIONS = (EXPECT_EXISTS, EXPECT_CONTAINS, EXPECT_CHANGED)

SYMBOL_SEP = "::"


class GitAnchorError(Exception):
    """Configuration or repository problem — distinct from a check simply failing."""


def _safe_path(raw: str) -> str:
    """Reject anything that could escape the configured root."""
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GitAnchorError(f"unsafe path {raw!r}")
    return str(candidate)


def split_anchor(anchor: str) -> tuple[str, str]:
    path, _, symbol = anchor.partition(SYMBOL_SEP)
    return _safe_path(path), symbol


def _repo(root: str):
    if not root:
        raise GitAnchorError(
            "no repository configured; set BLUEPRINT_REPO_ROOT to use git done-checks"
        )
    try:
        import git
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise GitAnchorError(f"GitPython unavailable: {e}") from e
    try:
        return git.Repo(root, search_parent_directories=False)
    except Exception as e:
        raise GitAnchorError(f"{root} is not a git repository: {type(e).__name__}") from e


def read_blob(root: str, path: str, ref: str = "HEAD") -> str | None:
    """File contents at a ref, or None when the path is not in that tree."""
    repo = _repo(root)
    try:
        blob = repo.commit(ref).tree / path
    except Exception:
        return None
    try:
        return blob.data_stream.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def anchor_sha(root: str, anchor: str, ref: str = "HEAD") -> str | None:
    """Hash of what the anchor names at a ref.

    For a bare path this is the file's content hash. For `path::symbol` it is the
    span hash the compile-mode compiler produces, so editing an unrelated function
    in the same file does not register as a change to this one.
    """
    path, symbol = split_anchor(anchor)
    source = read_blob(root, path, ref)
    if source is None:
        return None
    if not symbol:
        return hashlib.sha256(source.encode("utf-8")).hexdigest()
    for unit in compile_source(path, source):
        if unit.symbol == symbol:
            return unit.sha
    return None


def evaluate(root: str, anchor: str, expect: str, value: str | None, baseline: str | None):
    """Returns (passed, reason). A reason is only meaningful when it fails."""
    if expect not in EXPECTATIONS:
        return False, f"unknown git expectation {expect!r}; use one of {list(EXPECTATIONS)}"

    path, symbol = split_anchor(anchor)

    if expect == EXPECT_EXISTS:
        found = read_blob(root, path) is not None
        return found, "" if found else f"{path} is not in HEAD"

    if expect == EXPECT_CONTAINS:
        if not value:
            return False, "a contains check needs a value"
        source = read_blob(root, path)
        if source is None:
            return False, f"{path} is not in HEAD"
        return (value in source), "" if value in source else f"{path} does not contain {value!r}"

    # EXPECT_CHANGED
    if baseline is None:
        return False, f"no baseline recorded for {anchor}; nothing to compare against"
    current = anchor_sha(root, anchor)
    if current is None:
        target = f"{path}::{symbol}" if symbol else path
        return False, f"{target} is no longer in HEAD"
    if current == baseline:
        target = f"{symbol} in {path}" if symbol else path
        return False, f"{target} is unchanged since this task was created"
    return True, ""
