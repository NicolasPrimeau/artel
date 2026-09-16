import argparse
import collections
import os
import pathlib
import re
import sqlite3
import sys

SESSION_TAG = re.compile(r"session:([0-9a-f-]{16,})")
MIN_EVIDENCE = 5
MIN_AGREEMENT = 0.95


def _sessions_on_disk(root: pathlib.Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {f.stem: d.name for d in root.iterdir() if d.is_dir() for f in d.glob("*.jsonl")}


def _derive_mapping(db: sqlite3.Connection, sess2dir: dict[str, str]) -> dict[str, str]:
    votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    rows = db.execute(
        "SELECT project, tags FROM memory "
        "WHERE deleted_at IS NULL AND project IS NOT NULL AND tags LIKE '%session:%'"
    )
    for r in rows:
        m = SESSION_TAG.search(r["tags"] or "")
        if m and m.group(1) in sess2dir:
            votes[sess2dir[m.group(1)]][r["project"]] += 1
    mapping = {}
    for directory, counter in votes.items():
        total = sum(counter.values())
        project, n = counter.most_common(1)[0]
        if total >= MIN_EVIDENCE and n / total >= MIN_AGREEMENT:
            mapping[directory] = project
    return mapping


def _mapping_by_name(db: sqlite3.Connection, directories: set[str]) -> dict[str, str]:
    known = {
        r["project_id"].lower(): r["project_id"]
        for r in db.execute("SELECT DISTINCT project_id FROM project_members")
    }
    out = {}
    for directory in directories:
        name = directory.rsplit("-", 1)[-1].lower()
        if name in known:
            out[directory] = known[name]
    return out


def _plan(db: sqlite3.Connection, sess2dir: dict[str, str], mapping: dict[str, str]):
    todo: list[tuple[str, str]] = []
    skipped: collections.Counter = collections.Counter()
    rows = db.execute(
        "SELECT id, tags FROM memory "
        "WHERE deleted_at IS NULL AND project IS NULL AND tags LIKE '%session:%'"
    )
    for r in rows:
        m = SESSION_TAG.search(r["tags"] or "")
        if not m:
            skipped["no session tag parsed"] += 1
            continue
        directory = sess2dir.get(m.group(1))
        if directory is None:
            skipped["transcript no longer on disk"] += 1
        elif directory not in mapping:
            skipped[f"no labelled evidence for {directory}"] += 1
        else:
            todo.append((mapping[directory], r["id"]))
    return todo, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "artel.db"))
    ap.add_argument("--transcripts", default=str(pathlib.Path.home() / ".claude" / "projects"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    sess2dir = _sessions_on_disk(pathlib.Path(args.transcripts))
    if not sess2dir:
        print(f"no transcripts under {args.transcripts}", file=sys.stderr)
        return 1

    mapping = _derive_mapping(db, sess2dir)
    # A directory whose name is exactly an existing project with members is not a
    # guess -- it is a second kind of evidence. Without it, a project that simply
    # has few labelled entries yet stays unattributed forever.
    by_name = _mapping_by_name(db, set(sess2dir.values()) - set(mapping))
    mapping.update(by_name)
    if not mapping:
        print("no directory reached the evidence threshold", file=sys.stderr)
        return 1

    print(f"transcripts: {len(sess2dir)} sessions")
    print("mapping derived from already-labelled entries:")
    for directory, project in sorted(mapping.items()):
        how = "name match" if directory in by_name else "labelled entries"
        print(f"  {directory:<44} -> {project:<10} ({how})")

    todo, skipped = _plan(db, sess2dir, mapping)
    by_project = collections.Counter(project for project, _ in todo)
    print(f"\nwould set project on {len(todo)} entries:")
    for project, n in by_project.most_common():
        print(f"  {n:>6}  {project}")
    print("left alone:")
    for reason, n in skipped.most_common():
        print(f"  {n:>6}  {reason}")

    if not args.apply:
        print("\ndry run. pass --apply to write.")
        return 0

    with db:
        db.executemany("UPDATE memory SET project = ? WHERE id = ? AND project IS NULL", todo)
    remaining = db.execute(
        "SELECT count(*) FROM memory WHERE deleted_at IS NULL AND project IS NULL"
    ).fetchone()[0]
    print(f"\napplied {len(todo)} updates. entries still unattributed: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
