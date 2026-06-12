import os
import secrets
import sqlite3

import sqlite_vec

from .schema import SCHEMA

_conn: sqlite3.Connection | None = None


def get_db(path: str | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is None:
        if path is None:
            path = os.environ.get("DB_PATH", "artel.db")
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.enable_load_extension(True)
        sqlite_vec.load(_conn)
        _conn.enable_load_extension(False)
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _init_vec_table(_conn)
        _init_fts_table(_conn)
    return _conn


def _init_fts_table(conn: sqlite3.Connection) -> None:
    # keyword half of hybrid retrieval: BM25 over content via FTS5 (built into SQLite —
    # still zero external dependencies). Backfills any rows written before the index existed.
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
        USING fts5(id UNINDEXED, content)
    """)
    conn.execute("""
        INSERT INTO memory_fts (id, content)
        SELECT m.id, m.content FROM memory m
        WHERE m.id NOT IN (SELECT id FROM memory_fts)
    """)
    conn.commit()


def fts_index(db: sqlite3.Connection, entry_id: str, content: str) -> None:
    """Insert or refresh one memory's row in the keyword index."""
    db.execute("DELETE FROM memory_fts WHERE id=?", (entry_id,))
    db.execute("INSERT INTO memory_fts (id, content) VALUES (?, ?)", (entry_id, content))


def norm_project(p: str | None) -> str | None:
    if p is None:
        return None
    s = p.strip()
    return s.lower() if s else None


def instance_id() -> str:
    db = get_db()
    row = db.execute("SELECT value FROM kv WHERE key='instance_id'").fetchone()
    if row:
        return row["value"]
    iid = "artel-" + secrets.token_hex(8)
    with db:
        db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('instance_id', ?)", (iid,))
    return db.execute("SELECT value FROM kv WHERE key='instance_id'").fetchone()["value"]


class AmbiguousId(Exception):
    pass


_RESOLVABLE_TABLES = {"tasks", "memory", "task_comments", "messages", "agents"}
_MIN_PREFIX_LEN = 4


def resolve_id(table: str, ident: str) -> str | None:
    if table not in _RESOLVABLE_TABLES:
        raise ValueError(f"id resolution not allowed for table {table}")
    db = get_db()
    if db.execute(f"SELECT 1 FROM {table} WHERE id=?", (ident,)).fetchone():
        return ident
    if len(ident) < _MIN_PREFIX_LEN:
        return None
    pattern = ident.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db.execute(
        f"SELECT id FROM {table} WHERE id LIKE ? ESCAPE '\\' LIMIT 2", (pattern,)
    ).fetchall()
    if len(rows) > 1:
        raise AmbiguousId(ident)
    return rows[0]["id"] if rows else None


def _migrate(conn: sqlite3.Connection) -> None:
    agent_cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "project" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN project TEXT")
        conn.commit()
    if "last_seen_at" not in agent_cols:
        conn.execute("ALTER TABLE agents ADD COLUMN last_seen_at TEXT")
        conn.commit()
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "expected_outcome" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN expected_outcome TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if "tags" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    mem_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory)").fetchall()}
    if "expires_at" not in mem_cols:
        conn.execute("ALTER TABLE memory ADD COLUMN expires_at TEXT")
        conn.commit()
    if "origin" not in mem_cols:
        conn.execute("ALTER TABLE memory ADD COLUMN origin TEXT")
        conn.commit()
    if "read_count" not in mem_cols:
        conn.execute("ALTER TABLE memory ADD COLUMN read_count INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "last_read_at" not in mem_cols:
        conn.execute("ALTER TABLE memory ADD COLUMN last_read_at TEXT")
        conn.commit()
    if "distinct_reader_count" not in mem_cols:
        conn.execute(
            "ALTER TABLE memory ADD COLUMN distinct_reader_count INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    if "role" not in agent_cols:
        conn.execute(
            "ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT 'agent' CHECK (role IN ('owner', 'agent'))"
        )
        conn.commit()
    agents_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agents'"
    ).fetchone()
    if agents_sql and "CHECK (role IN" in agents_sql[0]:
        conn.executescript(
            """
            PRAGMA foreign_keys=off;
            BEGIN;
            ALTER TABLE agents RENAME TO _agents_old;
            CREATE TABLE agents (
                id            TEXT PRIMARY KEY,
                api_key       TEXT NOT NULL UNIQUE,
                project       TEXT,
                created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                last_seen_at  TEXT,
                role          TEXT NOT NULL DEFAULT 'agent'
            );
            INSERT INTO agents (id, api_key, project, created_at, last_seen_at, role)
                SELECT id, api_key, project, created_at, last_seen_at, role FROM _agents_old;
            DROP TABLE _agents_old;
            COMMIT;
            PRAGMA foreign_keys=on;
            """
        )
        conn.commit()

    feed_subs_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='feed_subscriptions'"
    ).fetchone()
    if feed_subs_sql and "project      TEXT NOT NULL" in feed_subs_sql[0]:
        conn.executescript("""
            PRAGMA foreign_keys=off;
            BEGIN;
            ALTER TABLE feed_subscriptions RENAME TO _feed_subs_old;
            CREATE TABLE feed_subscriptions (
                id           TEXT PRIMARY KEY,
                agent_id     TEXT NOT NULL,
                project      TEXT,
                url          TEXT NOT NULL,
                name         TEXT NOT NULL,
                tags         TEXT NOT NULL DEFAULT '[]',
                interval_min INTEGER NOT NULL DEFAULT 30,
                max_per_poll INTEGER NOT NULL DEFAULT 20,
                last_fetched_at TEXT,
                created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            INSERT INTO feed_subscriptions SELECT * FROM _feed_subs_old;
            DROP TABLE _feed_subs_old;
            COMMIT;
            PRAGMA foreign_keys=on;
        """)
        conn.commit()
    peer_links_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='peer_links'"
    ).fetchone()
    if peer_links_sql and "project     TEXT NOT NULL" in peer_links_sql[0]:
        conn.executescript("""
            PRAGMA foreign_keys=off;
            BEGIN;
            ALTER TABLE peer_links RENAME TO _peer_links_old;
            CREATE TABLE peer_links (
                id          TEXT PRIMARY KEY,
                peer_url    TEXT NOT NULL,
                project     TEXT,
                feed_id     TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            INSERT INTO peer_links SELECT * FROM _peer_links_old;
            DROP TABLE _peer_links_old;
            COMMIT;
            PRAGMA foreign_keys=on;
        """)
        conn.commit()

    memory_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory'"
    ).fetchone()
    if memory_sql and "'skill'" not in memory_sql[0]:
        conn.executescript("""
            PRAGMA foreign_keys=off;
            BEGIN;
            ALTER TABLE memory RENAME TO _memory_old;
            CREATE TABLE memory (
                id          TEXT PRIMARY KEY,
                type        TEXT NOT NULL CHECK (type IN ('memory','doc','directive','skill')),
                agent_id    TEXT NOT NULL,
                project     TEXT,
                scope       TEXT NOT NULL DEFAULT 'project' CHECK (scope IN ('agent','project')),
                content     TEXT NOT NULL,
                confidence  REAL NOT NULL DEFAULT 1.0,
                parents     TEXT NOT NULL DEFAULT '[]',
                tags        TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                version      INTEGER NOT NULL DEFAULT 1,
                deleted_at   TEXT,
                expires_at   TEXT,
                origin       TEXT,
                read_count   INTEGER NOT NULL DEFAULT 0,
                last_read_at TEXT,
                distinct_reader_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO memory SELECT * FROM _memory_old;
            DROP TABLE _memory_old;
            COMMIT;
            PRAGMA foreign_keys=on;
        """)
        conn.commit()

    _canonicalize_projects(conn)
    _migrate_project_roles(conn)


def _migrate_project_roles(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(project_members)").fetchall()}
    if "role" not in cols:
        conn.execute("ALTER TABLE project_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
        conn.commit()
    if conn.execute("SELECT value FROM kv WHERE key='project_roles_v1'").fetchone():
        return
    with conn:
        # bootstrap legacy projects: the earliest member becomes the owner
        for p in conn.execute("SELECT DISTINCT project_id FROM project_members").fetchall():
            first = conn.execute(
                "SELECT agent_id FROM project_members WHERE project_id=? "
                "ORDER BY joined_at ASC, agent_id ASC LIMIT 1",
                (p["project_id"],),
            ).fetchone()
            if first:
                conn.execute(
                    "UPDATE project_members SET role='owner' WHERE project_id=? AND agent_id=?",
                    (p["project_id"], first["agent_id"]),
                )
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('project_roles_v1', '1')")


def _canonicalize_projects(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM kv WHERE key='project_canonicalized_v1'").fetchone()
    if row:
        return

    simple_tables = [
        "memory",
        "tasks",
        "agents",
        "feed_subscriptions",
        "peer_links",
        "mesh_tokens",
        "archivist_metrics",
    ]
    with conn:
        for table in simple_tables:
            conn.execute(
                f"UPDATE {table} SET project = LOWER(TRIM(project)) WHERE project IS NOT NULL AND project != LOWER(TRIM(project))"
            )

        rows = conn.execute(
            "SELECT project_id, agent_id, joined_at FROM project_members"
        ).fetchall()
        deduped: dict[tuple[str, str], str] = {}
        for r in rows:
            pid = (r["project_id"] or "").strip().lower()
            if not pid:
                continue
            key = (pid, r["agent_id"])
            existing = deduped.get(key)
            if existing is None or r["joined_at"] < existing:
                deduped[key] = r["joined_at"]
        conn.execute("DELETE FROM project_members")
        for (pid, aid), joined in deduped.items():
            conn.execute(
                "INSERT INTO project_members (project_id, agent_id, joined_at) VALUES (?, ?, ?)",
                (pid, aid, joined),
            )

        brief_rows = conn.execute(
            "SELECT id, project, updated_at FROM memory "
            "WHERE type='doc' AND deleted_at IS NULL "
            "  AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value='project-brief') "
            "ORDER BY project, updated_at DESC"
        ).fetchall()
        seen_projects: set[str] = set()
        for br in brief_rows:
            proj = (br["project"] or "").lower()
            if not proj:
                continue
            if proj in seen_projects:
                conn.execute(
                    "UPDATE memory SET deleted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                    (br["id"],),
                )
            else:
                seen_projects.add(proj)

        conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES ('project_canonicalized_v1', '1')"
        )


def _init_vec_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec
        USING vec0(
            id TEXT PRIMARY KEY,
            embedding FLOAT[384]
        )
    """)
    conn.commit()
