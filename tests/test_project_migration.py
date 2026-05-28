import sqlite3

import pytest
import sqlite_vec

from artel.store.db import _canonicalize_projects
from artel.store.schema import SCHEMA


@pytest.fixture
def raw_conn(tmp_path):
    path = str(tmp_path / "mig.db")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def test_migration_lowercases_memory_projects(raw_conn):
    raw_conn.execute(
        "INSERT INTO memory (id, type, agent_id, project, content) VALUES ('m1', 'memory', 'a', 'Nimbus', 'x')"
    )
    raw_conn.execute(
        "INSERT INTO memory (id, type, agent_id, project, content) VALUES ('m2', 'memory', 'a', 'NIMBUS', 'y')"
    )
    raw_conn.execute(
        "INSERT INTO memory (id, type, agent_id, project, content) VALUES ('m3', 'memory', 'a', 'nimbus', 'z')"
    )
    raw_conn.commit()

    _canonicalize_projects(raw_conn)

    projects = sorted(
        {r["project"] for r in raw_conn.execute("SELECT project FROM memory").fetchall()}
    )
    assert projects == ["nimbus"]


def test_migration_dedupes_project_members(raw_conn):
    raw_conn.execute(
        "INSERT INTO project_members (project_id, agent_id, joined_at) VALUES ('Nimbus', 'alice', '2026-01-01')"
    )
    raw_conn.execute(
        "INSERT INTO project_members (project_id, agent_id, joined_at) VALUES ('nimbus', 'alice', '2026-02-01')"
    )
    raw_conn.execute(
        "INSERT INTO project_members (project_id, agent_id, joined_at) VALUES ('NIMBUS', 'bob', '2026-03-01')"
    )
    raw_conn.commit()

    _canonicalize_projects(raw_conn)

    rows = raw_conn.execute(
        "SELECT project_id, agent_id, joined_at FROM project_members ORDER BY agent_id"
    ).fetchall()
    assert [(r["project_id"], r["agent_id"]) for r in rows] == [
        ("nimbus", "alice"),
        ("nimbus", "bob"),
    ]
    alice = next(r for r in rows if r["agent_id"] == "alice")
    assert alice["joined_at"] == "2026-01-01"


def test_migration_dedupes_project_briefs(raw_conn):
    raw_conn.execute(
        "INSERT INTO memory (id, type, agent_id, project, content, tags, updated_at) "
        "VALUES ('b1', 'doc', 'archivist', 'Nimbus', 'old brief', '[\"project-brief\"]', '2026-01-01')"
    )
    raw_conn.execute(
        "INSERT INTO memory (id, type, agent_id, project, content, tags, updated_at) "
        "VALUES ('b2', 'doc', 'archivist', 'nimbus', 'new brief', '[\"project-brief\"]', '2026-02-01')"
    )
    raw_conn.commit()

    _canonicalize_projects(raw_conn)

    rows = raw_conn.execute(
        "SELECT id, deleted_at FROM memory WHERE project='nimbus' ORDER BY updated_at DESC"
    ).fetchall()
    assert rows[0]["id"] == "b2"
    assert rows[0]["deleted_at"] is None
    assert rows[1]["id"] == "b1"
    assert rows[1]["deleted_at"] is not None


def test_migration_is_idempotent(raw_conn):
    raw_conn.execute(
        "INSERT INTO memory (id, type, agent_id, project, content) VALUES ('m1', 'memory', 'a', 'FOO', 'x')"
    )
    raw_conn.commit()

    _canonicalize_projects(raw_conn)
    first = raw_conn.execute("SELECT project FROM memory WHERE id='m1'").fetchone()["project"]

    _canonicalize_projects(raw_conn)
    second = raw_conn.execute("SELECT project FROM memory WHERE id='m1'").fetchone()["project"]

    assert first == second == "foo"
    flag = raw_conn.execute("SELECT value FROM kv WHERE key='project_canonicalized_v1'").fetchone()
    assert flag["value"] == "1"
