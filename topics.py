from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone

import db

PALETTE = [
    "#58a6ff", "#3fb950", "#f85149", "#d29922", "#bc8cff",
    "#ff7b72", "#79c0ff", "#56d364", "#ffa657", "#f0883e",
    "#db61a2", "#7ee787",
]


TAG_DEPENDENT_TABLES = {
    "tag_links": """
        CREATE TABLE tag_links (
            from_tag_id INTEGER NOT NULL REFERENCES tags(id),
            to_tag_id   INTEGER NOT NULL REFERENCES tags(id),
            note        TEXT,
            source      TEXT NOT NULL,
            user_id     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (from_tag_id, to_tag_id)
        )
    """,
    "tag_notes": """
        CREATE TABLE tag_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id      INTEGER NOT NULL REFERENCES tags(id),
            note_date   TEXT NOT NULL,
            content     TEXT NOT NULL,
            user_id     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            is_archived INTEGER NOT NULL DEFAULT 0
        )
    """,
    "object_tags": """
        CREATE TABLE object_tags (
            object_kind       TEXT NOT NULL,
            object_id         TEXT NOT NULL,
            tag_id            INTEGER NOT NULL REFERENCES tags(id),
            tag_source        TEXT NOT NULL DEFAULT 'intake_inferred',
            source_session_id TEXT,
            user_id           INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT NOT NULL,
            PRIMARY KEY (object_kind, object_id, tag_id)
        )
    """,
    "object_highlights": """
        CREATE TABLE object_highlights (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            object_kind TEXT NOT NULL,
            object_id   TEXT NOT NULL,
            tag_id      INTEGER NOT NULL REFERENCES tags(id),
            sentence    TEXT NOT NULL,
            user_id     INTEGER NOT NULL DEFAULT 1,
            source      TEXT NOT NULL DEFAULT 'intake'
        )
    """,
    "chat_scope_tags": """
        CREATE TABLE chat_scope_tags (
            chat_id  INTEGER NOT NULL REFERENCES chats(id),
            tag_id   INTEGER NOT NULL REFERENCES tags(id),
            added_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, tag_id)
        )
    """,
}


TAG_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS idx_tag_notes_tag      ON tag_notes(tag_id);
    CREATE INDEX IF NOT EXISTS idx_tag_notes_date     ON tag_notes(note_date);
    CREATE INDEX IF NOT EXISTS idx_object_tags_obj    ON object_tags(object_kind, object_id);
    CREATE INDEX IF NOT EXISTS idx_object_tags_tag    ON object_tags(tag_id);
    CREATE INDEX IF NOT EXISTS idx_obj_hl_obj         ON object_highlights(object_kind, object_id);
    CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id);
"""


def _table_refs_legacy_tags(conn, table: str) -> bool:
    return any(r["table"] == "_tags_old" for r in conn.execute(f"PRAGMA foreign_key_list({table})"))


def _copy_common_columns(conn, table: str, backup: str) -> None:
    old_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({backup})")}
    new_cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    common = [c for c in new_cols if c in old_cols]
    col_sql = ", ".join(common)
    conn.execute(
        f"INSERT INTO {table} ({col_sql}) SELECT {col_sql} FROM {backup}"
    )


def _repair_legacy_tag_foreign_keys(conn) -> None:
    """Repair tables left pointing at _tags_old by older SQLite table rebuilds."""
    affected = [table for table in TAG_DEPENDENT_TABLES if _table_refs_legacy_tags(conn, table)]
    if not affected:
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for table in affected:
            backup = f"_{table}_legacy_fk"
            conn.execute(f"ALTER TABLE {table} RENAME TO {backup}")
            conn.execute(TAG_DEPENDENT_TABLES[table])
            _copy_common_columns(conn, table, backup)
            conn.execute(f"DROP TABLE {backup}")
        conn.executescript(TAG_INDEX_SQL)
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def init_db():
    migrations = [
        # Version 1: Initial schema
        """
        CREATE TABLE IF NOT EXISTS tags (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kind          TEXT NOT NULL DEFAULT 'topic',
            name          TEXT NOT NULL,
            description   TEXT,
            summary       TEXT,
            color         TEXT NOT NULL DEFAULT '#58a6ff',
            keywords      TEXT,
            embedding     TEXT,
            parent_tag_id INTEGER REFERENCES tags(id),
            user_id       INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE(user_id, kind, name)
        );

        CREATE TABLE IF NOT EXISTS tag_links (
            from_tag_id INTEGER NOT NULL REFERENCES tags(id),
            to_tag_id   INTEGER NOT NULL REFERENCES tags(id),
            note        TEXT,
            source      TEXT NOT NULL,
            user_id     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (from_tag_id, to_tag_id)
        );

        CREATE TABLE IF NOT EXISTS tag_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id      INTEGER NOT NULL REFERENCES tags(id),
            note_date   TEXT NOT NULL,
            content     TEXT NOT NULL,
            user_id     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            is_archived INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS object_tags (
            object_kind       TEXT NOT NULL,
            object_id         TEXT NOT NULL,
            tag_id            INTEGER NOT NULL REFERENCES tags(id),
            tag_source        TEXT NOT NULL DEFAULT 'intake_inferred',
            source_session_id TEXT,
            user_id           INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT NOT NULL,
            PRIMARY KEY (object_kind, object_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS object_highlights (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            object_kind TEXT NOT NULL,
            object_id   TEXT NOT NULL,
            tag_id      INTEGER NOT NULL REFERENCES tags(id),
            sentence    TEXT NOT NULL,
            user_id     INTEGER NOT NULL DEFAULT 1,
            source      TEXT NOT NULL DEFAULT 'intake'
        );

        CREATE TABLE IF NOT EXISTS chats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT 'New Chat',
            user_id    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL REFERENCES chats(id),
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_scope_tags (
            chat_id  INTEGER NOT NULL REFERENCES chats(id),
            tag_id   INTEGER NOT NULL REFERENCES tags(id),
            added_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS chat_actions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL REFERENCES chats(id),
            action       TEXT NOT NULL,
            object_kind  TEXT,
            object_id    TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            user_id    INTEGER NOT NULL DEFAULT 1,
            key        TEXT NOT NULL,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS extraction_runs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            input_id   TEXT NOT NULL,
            summary    TEXT,
            user_id    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS extraction_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER NOT NULL REFERENCES extraction_runs(id),
            item_type    TEXT NOT NULL,
            item_id      TEXT NOT NULL,
            name         TEXT NOT NULL,
            soft_deleted INTEGER NOT NULL DEFAULT 0,
            user_id      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_extraction_runs_input ON extraction_runs(input_id);
        CREATE INDEX IF NOT EXISTS idx_extraction_items_run ON extraction_items(run_id);
        """ + TAG_INDEX_SQL,
        # Version 2: Multi-user support and content_type migration
        """
        -- We handle these in the idempotent block below
        """
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)

    # Migrations: add columns if not yet present
    with db.get_db() as conn:
        for table, col, def_val in [
            ("tags", "user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("tag_links", "user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("tag_notes", "user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("object_tags", "user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("object_highlights", "user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("chats", "user_id", "INTEGER NOT NULL DEFAULT 1"),
            ("chat_messages", "content_type", "TEXT NOT NULL DEFAULT 'text'"),
            ("settings", "user_id", "INTEGER NOT NULL DEFAULT 1"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {def_val}")
            except Exception:
                pass  # Column already exists
        _repair_legacy_tag_foreign_keys(conn)


# ---------------------------------------------------------------------------
# Topics CRUD  (tags with kind='topic')
# ---------------------------------------------------------------------------

def create_topic(user_id, name, description=None, color=None, parent_tag_id=None):
    now = datetime.now(timezone.utc).isoformat()
    if color is None:
        color = random.choice(PALETTE)
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tags (kind, name, description, color, parent_tag_id, user_id, created_at, updated_at) "
            "VALUES ('topic', ?, ?, ?, ?, ?, ?, ?)",
            (name, description, color, parent_tag_id, user_id, now, now),
        )
        row = conn.execute(
            "SELECT id FROM tags WHERE kind='topic' AND name=? AND user_id=?", (name, user_id)
        ).fetchone()
        return row["id"]


def get_topic(user_id, topic_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tags WHERE id=? AND kind='topic' AND user_id=?", (topic_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def list_topics(user_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT t.*,
                   COUNT(CASE WHEN tn.is_archived=0 THEN 1 END) AS entry_count,
                   MAX(tn.note_date) AS last_activity,
                   p.name AS parent_name
            FROM tags t
            LEFT JOIN tag_notes tn ON tn.tag_id = t.id
            LEFT JOIN tags p ON p.id = t.parent_tag_id
            WHERE t.kind = 'topic' AND t.user_id = ?
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_root_topics(user_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, name, color, description
            FROM tags
            WHERE user_id=? AND kind='topic' AND parent_tag_id IS NULL
            ORDER BY name COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_topic(user_id, topic_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "summary", "color", "keywords", "parent_tag_id")}
    if not allowed:
        return
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k}=?" for k in allowed)
    vals = list(allowed.values()) + [topic_id, user_id]
    with db.get_db() as conn:
        conn.execute(f"UPDATE tags SET {sets} WHERE id=? AND kind='topic' AND user_id=?", vals)


def store_tag_embedding(tag_id, embedding_json):
    """Store a serialized embedding vector (JSON string) for any tag."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tags SET embedding=?, updated_at=? WHERE id=?",
            (embedding_json, datetime.now(timezone.utc).isoformat(), tag_id),
        )


def list_tags_missing_embeddings():
    """Return all tags that have no embedding yet — used for backfill."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, kind, name, description, keywords FROM tags WHERE embedding IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_topic(user_id, topic_id):
    with db.get_db() as conn:
        # Verify ownership
        row = conn.execute("SELECT id FROM tags WHERE id=? AND user_id=?", (topic_id, user_id)).fetchone()
        if not row:
            return
        conn.execute(
            "DELETE FROM chat_messages WHERE chat_id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (topic_id,)
        )
        conn.execute(
            "DELETE FROM chats WHERE id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (topic_id,)
        )
        conn.execute("DELETE FROM tag_notes WHERE tag_id=?", (topic_id,))
        conn.execute("DELETE FROM object_tags WHERE tag_id=?", (topic_id,))
        conn.execute("DELETE FROM object_highlights WHERE tag_id=?", (topic_id,))
        conn.execute("DELETE FROM tags WHERE id=? AND kind='topic'", (topic_id,))


# ---------------------------------------------------------------------------
# Tag notes  (was topic_entries)
# ---------------------------------------------------------------------------

def upsert_topic_entry(user_id, topic_id, journal_date, content):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id FROM tag_notes WHERE tag_id=? AND note_date=? AND is_archived=0",
            (topic_id, journal_date),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tag_notes SET content=?, created_at=? WHERE id=?",
                (content, now, row["id"]),
            )
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO tag_notes (tag_id, note_date, content, user_id, created_at, is_archived) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (topic_id, journal_date, content, user_id, now),
            )
            return cur.lastrowid


def list_topic_entries(topic_id, include_archived=False):
    with db.get_db() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM tag_notes WHERE tag_id=? ORDER BY note_date DESC, id DESC",
                (topic_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tag_notes WHERE tag_id=? AND is_archived=0 "
                "ORDER BY note_date DESC, id DESC",
                (topic_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def archive_topic_entries(topic_id, keep_ids=None):
    keep_ids = keep_ids or []
    with db.get_db() as conn:
        if keep_ids:
            placeholders = ",".join("?" * len(keep_ids))
            conn.execute(
                f"UPDATE tag_notes SET is_archived=1 "
                f"WHERE tag_id=? AND is_archived=0 AND id NOT IN ({placeholders})",
                [topic_id] + keep_ids,
            )
        else:
            conn.execute(
                "UPDATE tag_notes SET is_archived=1 WHERE tag_id=? AND is_archived=0",
                (topic_id,),
            )


def get_all_entry_content(topic_id, include_archived=False):
    entries = list_topic_entries(topic_id, include_archived=include_archived)
    parts = []
    for e in entries:
        parts.append(f"### {e['note_date']}\n\n{e['content']}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Object highlights  (was entry_highlights)
# ---------------------------------------------------------------------------

def store_highlights(user_id, entry_id: str, topic_id: int, sentences: list) -> None:
    """Store key sentences for an input/tag pair (replaces any existing)."""
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM object_highlights WHERE object_kind='input' AND object_id=? AND tag_id=?",
            (entry_id, topic_id),
        )
        for sentence in sentences:
            s = sentence.strip() if sentence else ""
            if len(s) >= 10:
                conn.execute(
                    "INSERT INTO object_highlights (object_kind, object_id, tag_id, sentence, user_id, source) "
                    "VALUES ('input', ?, ?, ?, ?, 'intake')",
                    (entry_id, topic_id, s, user_id),
                )


def get_highlights_for_entry(entry_id: str) -> list:
    """Return all highlights for an entry, joined with tag color, name and kind."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT oh.sentence, oh.tag_id AS topic_id, t.name, t.color, t.kind "
            "FROM object_highlights oh JOIN tags t ON oh.tag_id = t.id "
            "WHERE oh.object_kind='input' AND oh.object_id=? ORDER BY oh.tag_id, oh.id",
            (entry_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Object tags  (was entry_topic_tags)
# ---------------------------------------------------------------------------

def tag_entry(user_id, entry_id: str, topic_ids: list, tag_source: str = "intake_inferred") -> None:
    """Tag a journal input with topics."""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        for tid in topic_ids:
            conn.execute(
                "INSERT OR IGNORE INTO object_tags "
                "(object_kind, object_id, tag_id, tag_source, user_id, created_at) "
                "VALUES ('input', ?, ?, ?, ?, ?)",
                (entry_id, tid, tag_source, user_id, now),
            )


def get_tags_for_entry(entry_id: str) -> list:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.color, t.kind FROM object_tags ot "
            "JOIN tags t ON ot.tag_id = t.id "
            "WHERE ot.object_kind='input' AND ot.object_id=? ORDER BY t.name",
            (entry_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_tags_for_entries(entry_ids: list) -> dict:
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT ot.object_id AS entry_id, t.id, t.name, t.color, t.kind "
            f"FROM object_tags ot JOIN tags t ON ot.tag_id = t.id "
            f"WHERE ot.object_kind='input' AND ot.object_id IN ({placeholders}) ORDER BY t.name",
            list(entry_ids),
        ).fetchall()
    result: dict = {}
    for row in rows:
        eid = row["entry_id"]
        if eid not in result:
            result[eid] = []
        result[eid].append({"id": row["id"], "name": row["name"], "color": row["color"], "kind": row["kind"]})
    return result


# ---------------------------------------------------------------------------
# Task tags  (object_kind = 'task')
# ---------------------------------------------------------------------------

def tag_task(user_id, task_id: str, tag_ids: list, tag_source: str = "user_explicit") -> None:
    """Tag a task with topics/entities."""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        for tid in tag_ids:
            conn.execute(
                "INSERT OR IGNORE INTO object_tags "
                "(object_kind, object_id, tag_id, tag_source, user_id, created_at) "
                "VALUES ('task', ?, ?, ?, ?, ?)",
                (task_id, tid, tag_source, user_id, now),
            )


def untag_task(task_id: str, tag_id: int) -> None:
    """Remove a tag from a task."""
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM object_tags WHERE object_kind='task' AND object_id=? AND tag_id=?",
            (task_id, tag_id),
        )


def untag_entry(entry_id: str, tag_id: int) -> None:
    """Remove a tag from an input/entry."""
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM object_tags WHERE object_kind='input' AND object_id=? AND tag_id=?",
            (entry_id, tag_id),
        )


def get_tags_for_task(task_id: str) -> list:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.color, t.kind FROM object_tags ot "
            "JOIN tags t ON ot.tag_id = t.id "
            "WHERE ot.object_kind='task' AND ot.object_id=? ORDER BY t.name",
            (task_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_tags(user_id) -> list:
    """Return all topics and entities for autocomplete (id, name, kind, color)."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, kind, color, parent_tag_id FROM tags WHERE user_id=? ORDER BY name ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_tags_with_task_counts(user_id) -> list:
    """Return all tags with count of non-deleted tasks, sorted by task count desc."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.kind, t.color, t.parent_tag_id,
                   COUNT(DISTINCT tk.id) AS task_count
            FROM tags t
            LEFT JOIN object_tags ot ON ot.tag_id = t.id AND ot.object_kind = 'task'
            LEFT JOIN tasks tk ON tk.id = ot.object_id
                               AND COALESCE(tk.soft_deleted, 0) = 0
            WHERE t.user_id = ?
            GROUP BY t.id
            ORDER BY task_count DESC, t.name ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Move entry date  (updates tag_notes when an entry is re-dated)
# ---------------------------------------------------------------------------

def move_entry_date(old_date: str, new_date: str) -> None:
    """Update tag_notes rows when a journal entry moves to a new date."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tag_notes SET note_date=? WHERE note_date=?",
            (new_date, old_date),
        )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_topics(source_id, target_id):
    with db.get_db() as conn:
        # Migrate notes and tags to target
        conn.execute(
            "UPDATE tag_notes SET tag_id=? WHERE tag_id=?", (target_id, source_id)
        )
        conn.execute(
            "INSERT OR IGNORE INTO object_tags (object_kind, object_id, tag_id, tag_source, user_id, created_at) "
            "SELECT object_kind, object_id, ?, tag_source, user_id, created_at "
            "FROM object_tags WHERE tag_id=?",
            (target_id, source_id),
        )
        conn.execute("DELETE FROM object_tags WHERE tag_id=?", (source_id,))
        # Migrate highlights to target
        conn.execute(
            "UPDATE object_highlights SET tag_id=? WHERE tag_id=?", (target_id, source_id)
        )
        # Clean up source chats (must delete child rows before parents)
        conn.execute(
            "DELETE FROM chat_actions WHERE chat_id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (source_id,)
        )
        conn.execute(
            "DELETE FROM chat_messages WHERE chat_id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (source_id,)
        )
        conn.execute(
            "DELETE FROM chats WHERE id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (source_id,)
        )
        conn.execute("DELETE FROM chat_scope_tags WHERE tag_id=?", (source_id,))
        # Clean up tag links
        conn.execute(
            "DELETE FROM tag_links WHERE from_tag_id=? OR to_tag_id=?", (source_id, source_id)
        )
        conn.execute("DELETE FROM tags WHERE id=?", (source_id,))


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------

def list_chats_all(user_id):
    """Return all chats newest-first with comma-joined scope tag names."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at,
                   GROUP_CONCAT(t.name, ', ') AS scope_names
            FROM chats c
            LEFT JOIN chat_scope_tags cst ON cst.chat_id = c.id
            LEFT JOIN tags t ON t.id = cst.tag_id
            WHERE c.user_id = ?
            GROUP BY c.id ORDER BY c.id DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_scope_tags(chat_id):
    """Return [{id, name, color}] for all tags scoped to this chat."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.color FROM tags t "
            "JOIN chat_scope_tags cst ON cst.tag_id = t.id WHERE cst.chat_id=?",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_chat_unscoped(user_id, title="New Chat"):
    """Create a chat with no scope tags. Returns chat_id."""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chats (title, user_id, created_at) VALUES (?, ?, ?)", (title, user_id, now)
        )
        return cur.lastrowid


def rename_chat(chat_id, title):
    with db.get_db() as conn:
        conn.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))


def chat_has_been_saved(chat_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM chat_actions WHERE chat_id=? "
            "AND action IN ('saved_summary','saved_transcript') LIMIT 1",
            (chat_id,),
        ).fetchone()
        return row is not None


def log_chat_action(chat_id, action, object_kind=None, object_id=None, payload=None):
    """Append an action to the chat_actions ledger."""
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO chat_actions (chat_id, action, object_kind, object_id, payload_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (chat_id, action, object_kind, object_id,
             _json.dumps(payload) if payload else None, now),
        )


# ---------------------------------------------------------------------------
# Entities CRUD  (tags with kind='entity')
# ---------------------------------------------------------------------------

def create_entity(user_id, name, description=None, color=None, parent_tag_id=None):
    now = datetime.now(timezone.utc).isoformat()
    if color is None:
        color = random.choice(PALETTE)
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tags (kind, name, description, color, parent_tag_id, user_id, created_at, updated_at) "
            "VALUES ('entity', ?, ?, ?, ?, ?, ?, ?)",
            (name, description, color, parent_tag_id, user_id, now, now),
        )
        row = conn.execute(
            "SELECT id FROM tags WHERE kind='entity' AND name=? AND user_id=?", (name, user_id)
        ).fetchone()
        return row["id"]


def get_entity(user_id, entity_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tags WHERE id=? AND kind='entity' AND user_id=?", (entity_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def list_entities(user_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT t.*,
                   COUNT(CASE WHEN tn.is_archived=0 THEN 1 END) AS entry_count,
                   MAX(tn.note_date) AS last_activity,
                   p.name AS parent_name
            FROM tags t
            LEFT JOIN tag_notes tn ON tn.tag_id = t.id
            LEFT JOIN tags p ON p.id = t.parent_tag_id
            WHERE t.kind = 'entity' AND t.user_id = ?
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_entity(user_id, entity_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "summary", "color", "keywords", "parent_tag_id")}
    if not allowed:
        return
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k}=?" for k in allowed)
    vals = list(allowed.values()) + [entity_id, user_id]
    with db.get_db() as conn:
        conn.execute(f"UPDATE tags SET {sets} WHERE id=? AND kind='entity' AND user_id=?", vals)


def delete_entity(user_id, entity_id):
    with db.get_db() as conn:
        # Verify ownership
        row = conn.execute("SELECT id FROM tags WHERE id=? AND user_id=?", (entity_id, user_id)).fetchone()
        if not row:
            return
        conn.execute(
            "DELETE FROM chat_messages WHERE chat_id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (entity_id,)
        )
        conn.execute(
            "DELETE FROM chats WHERE id IN "
            "(SELECT chat_id FROM chat_scope_tags WHERE tag_id=?)", (entity_id,)
        )
        conn.execute("DELETE FROM tag_notes WHERE tag_id=?", (entity_id,))
        conn.execute("DELETE FROM object_tags WHERE tag_id=?", (entity_id,))
        conn.execute("DELETE FROM object_highlights WHERE tag_id=?", (entity_id,))
        conn.execute(
            "DELETE FROM tag_links WHERE from_tag_id=? OR to_tag_id=?",
            (entity_id, entity_id),
        )
        conn.execute("DELETE FROM tags WHERE id=? AND kind='entity'", (entity_id,))


def search_tags(user_id, query: str, kind: str | None = None, limit: int = 20) -> list:
    """Search tags by name using LIKE. Returns slim tag dicts."""
    with db.get_db() as conn:
        params: list = [user_id, f"%{query}%"]
        kind_clause = ""
        if kind:
            kind_clause = "AND kind=?"
            params.append(kind)
        params.append(limit)
        rows = conn.execute(
            f"SELECT id, kind, name, description, color FROM tags "
            f"WHERE user_id=? AND name LIKE ? {kind_clause} ORDER BY name COLLATE NOCASE LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tag links  (manual backlinks between any two tags)
# ---------------------------------------------------------------------------

def get_tag_links(tag_id):
    """Return all tags linked to/from this tag with metadata."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT tl.from_tag_id, tl.to_tag_id, tl.note, tl.source,
                   t.id AS linked_id, t.name AS linked_name,
                   t.kind AS linked_kind, t.color AS linked_color
            FROM tag_links tl
            JOIN tags t ON t.id = CASE
                WHEN tl.from_tag_id = ? THEN tl.to_tag_id
                ELSE tl.from_tag_id END
            WHERE tl.from_tag_id = ? OR tl.to_tag_id = ?
            """,
            (tag_id, tag_id, tag_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_children(tag_id):
    """Return all tags whose parent_tag_id = tag_id (direct children only)."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, kind, name, color, description FROM tags WHERE parent_tag_id=?",
            (tag_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_parent(tag_id):
    """Return the parent tag for tag_id, or None."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT p.id, p.kind, p.name, p.color, p.description "
            "FROM tags t JOIN tags p ON p.id = t.parent_tag_id "
            "WHERE t.id=?",
            (tag_id,),
        ).fetchone()
    return dict(row) if row else None


def get_ancestors(user_id, tag_id):
    ancestors = []
    seen = set()
    current_id = tag_id
    for _ in range(50):
        parent = get_parent(current_id)
        if not parent or parent["id"] in seen:
            break
        seen.add(parent["id"])
        ancestors.append(parent)
        current_id = parent["id"]
    return ancestors


def get_descendants(tag_id):
    """Return list of IDs of all descendants (BFS), not including tag_id itself."""
    visited = set()
    queue = [tag_id]
    while queue:
        current = queue.pop()
        for child in get_children(current):
            cid = child["id"]
            if cid not in visited:
                visited.add(cid)
                queue.append(cid)
    return list(visited)


def get_descendant_tag_ids(tag_id, include_self=True):
    ids = set(get_descendants(tag_id))
    if include_self:
        ids.add(tag_id)
    return list(ids)


def get_parent_tag_ids(tag_ids):
    if not tag_ids:
        return {}
    placeholders = ",".join("?" * len(tag_ids))
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT id, parent_tag_id FROM tags WHERE id IN ({placeholders})",
            list(tag_ids),
        ).fetchall()
    return {r["id"]: r["parent_tag_id"] for r in rows}


def get_related_tags(user_id, tag_id):
    exclude_ids = {tag_id}
    with db.get_db() as conn:
        row = conn.execute("SELECT parent_tag_id FROM tags WHERE id=? AND user_id=?", (tag_id, user_id)).fetchone()
        parent_id = row["parent_tag_id"] if row else None
        if parent_id:
            exclude_ids.add(parent_id)
            sibling_rows = conn.execute(
                "SELECT id FROM tags WHERE parent_tag_id=? AND id!=? AND user_id=?",
                (parent_id, tag_id, user_id),
            ).fetchall()
            exclude_ids.update(r["id"] for r in sibling_rows)
        child_rows = conn.execute("SELECT id FROM tags WHERE parent_tag_id=? AND user_id=?", (tag_id, user_id)).fetchall()
        exclude_ids.update(r["id"] for r in child_rows)
        rows = conn.execute(
            """
            SELECT t.id, t.kind, t.name, t.color, t.description, tl.note, tl.source
            FROM tag_links tl
            JOIN tags t ON t.id = CASE WHEN tl.from_tag_id=? THEN tl.to_tag_id ELSE tl.from_tag_id END
            WHERE (tl.from_tag_id=? OR tl.to_tag_id=?) AND t.user_id=?
            ORDER BY t.kind, t.name COLLATE NOCASE
            """,
            (tag_id, tag_id, tag_id, user_id),
        ).fetchall()
    return [dict(r) for r in rows if r["id"] not in exclude_ids]


def add_tag_link(user_id, from_tag_id, to_tag_id, note=None, source="user"):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tag_links (from_tag_id, to_tag_id, note, source, user_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (from_tag_id, to_tag_id, note, source, user_id, now),
        )


def remove_tag_link(from_tag_id, to_tag_id):
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM tag_links WHERE "
            "(from_tag_id=? AND to_tag_id=?) OR (from_tag_id=? AND to_tag_id=?)",
            (from_tag_id, to_tag_id, to_tag_id, from_tag_id),
        )


def add_scope_tag(chat_id, tag_id):
    """Add a tag to a chat's scope."""
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chat_scope_tags (chat_id, tag_id, added_at) VALUES (?,?,?)",
            (chat_id, tag_id, now),
        )


def create_chat(user_id, topic_id, title="New Chat"):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chats (title, user_id, created_at) VALUES (?, ?, ?)",
            (title, user_id, now),
        )
        chat_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chat_scope_tags (chat_id, tag_id, added_at) VALUES (?, ?, ?)",
            (chat_id, topic_id, now),
        )
        return chat_id


def list_chats(topic_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT c.id, c.title, c.created_at FROM chats c "
            "JOIN chat_scope_tags cst ON cst.chat_id = c.id "
            "WHERE cst.tag_id=? ORDER BY c.id DESC",
            (topic_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_chat(user_id, chat_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT c.id, c.title, c.created_at, cst.tag_id AS topic_id "
            "FROM chats c LEFT JOIN chat_scope_tags cst ON cst.chat_id = c.id "
            "WHERE c.id=? AND c.user_id=?",
            (chat_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def add_message(chat_id, role, content, content_type="text"):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content, created_at, content_type) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, now, content_type),
        )
        return cur.lastrowid


def add_tool_turn(chat_id, assistant_blocks, tool_result_blocks):
    """Persist one agent tool-use round: assistant content blocks + tool results."""
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content, created_at, content_type) VALUES (?, ?, ?, ?, ?)",
            (chat_id, "assistant", _json.dumps(assistant_blocks), now, "tool_turn"),
        )
        conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content, created_at, content_type) VALUES (?, ?, ?, ?, ?)",
            (chat_id, "user", _json.dumps(tool_result_blocks), now, "tool_results"),
        )


def get_messages(chat_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE chat_id=? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Settings  (key-value store for user preferences)
# ---------------------------------------------------------------------------

def get_setting(user_id, key: str, default: str = "") -> str:
    with db.get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)).fetchone()
    return row["value"] if row else default


def set_setting(user_id, key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO settings (user_id, key, value, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (user_id, key, value, now),
        )


def get_all_settings(user_id) -> dict:
    """Return all settings as a plain dict."""
    with db.get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE user_id=?", (user_id,)).fetchall()
    return {r["key"]: r["value"] for r in rows}


def store_extraction_run(user_id: int, input_id: str, summary: str | None, items: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO extraction_runs (input_id, summary, user_id, created_at) VALUES (?,?,?,?)",
            (input_id, summary, user_id, now),
        )
        run_id = cur.lastrowid
        for item in items:
            conn.execute(
                """
                INSERT INTO extraction_items (run_id, item_type, item_id, name, user_id, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (run_id, item["item_type"], str(item["item_id"]), item["name"], user_id, now),
            )
    return run_id


def get_extraction_for_entry(user_id: int, input_id: str) -> dict | None:
    with db.get_db() as conn:
        run = conn.execute(
            """
            SELECT * FROM extraction_runs
            WHERE user_id=? AND input_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, input_id),
        ).fetchone()
        if not run:
            return None
        run_dict = dict(run)
        rows = conn.execute(
            "SELECT * FROM extraction_items WHERE run_id=? ORDER BY id ASC",
            (run_dict["id"],),
        ).fetchall()
    run_dict["items"] = [dict(r) for r in rows]
    return run_dict


def get_extraction_item(user_id: int, item_id: int) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM extraction_items WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
    return dict(row) if row else None


def set_extraction_item_deleted(user_id: int, item_id: int, deleted: bool) -> None:
    with db.get_db() as conn:
        conn.execute(
            "UPDATE extraction_items SET soft_deleted=? WHERE id=? AND user_id=?",
            (1 if deleted else 0, item_id, user_id),
        )
