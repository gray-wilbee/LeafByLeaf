"""
One-time migration script: entries.db + topics.db → app.db

Run with the app STOPPED. Safe to re-run: removes app.db and recreates it.

Usage:
    python3 migrate.py

Reads JOURNAL_DIR from environment (same as the app). Prints a row-count
verification report at the end — confirm every count matches before restarting
the service.
"""

import os
import sqlite3
from datetime import datetime, timezone

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ENTRIES_DB = os.path.join(APP_DIR, "entries.db")
TOPICS_DB  = os.path.join(APP_DIR, "topics.db")
APP_DB     = os.path.join(APP_DIR, "app.db")


def main():
    # Verify source databases exist
    for path, name in [(ENTRIES_DB, "entries.db"), (TOPICS_DB, "topics.db")]:
        if not os.path.exists(path):
            print(f"ERROR: {name} not found at {path}")
            raise SystemExit(1)

    # Remove any previous attempt so the script is idempotent
    if os.path.exists(APP_DB):
        os.remove(APP_DB)
        print("Removed existing app.db")

    now_iso = datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # Create app.db with full new schema
    # -------------------------------------------------------------------------
    conn = sqlite3.connect(APP_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE inputs (
            id                TEXT PRIMARY KEY,
            source            TEXT NOT NULL DEFAULT 'journal',
            content           TEXT,
            occurred_at       TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            source_session_id TEXT,
            UNIQUE(source, occurred_at)
        );

        CREATE TABLE tags (
            id          INTEGER PRIMARY KEY,
            kind        TEXT NOT NULL DEFAULT 'topic',
            name        TEXT NOT NULL,
            description TEXT,
            summary     TEXT,
            color       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            UNIQUE(kind, name)
        );

        CREATE TABLE tag_links (
            from_tag_id INTEGER NOT NULL REFERENCES tags(id),
            to_tag_id   INTEGER NOT NULL REFERENCES tags(id),
            note        TEXT,
            source      TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (from_tag_id, to_tag_id)
        );

        CREATE TABLE tag_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id      INTEGER NOT NULL REFERENCES tags(id),
            note_date   TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            is_archived INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE object_tags (
            object_kind       TEXT NOT NULL,
            object_id         TEXT NOT NULL,
            tag_id            INTEGER NOT NULL REFERENCES tags(id),
            tag_source        TEXT NOT NULL DEFAULT 'intake_inferred',
            source_session_id TEXT,
            created_at        TEXT NOT NULL,
            PRIMARY KEY (object_kind, object_id, tag_id)
        );

        CREATE TABLE object_highlights (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            object_kind TEXT NOT NULL,
            object_id   TEXT NOT NULL,
            tag_id      INTEGER NOT NULL REFERENCES tags(id),
            sentence    TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'intake'
        );

        CREATE TABLE chats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT 'New Chat',
            created_at TEXT NOT NULL
        );

        CREATE TABLE chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL REFERENCES chats(id),
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE chat_scope_tags (
            chat_id  INTEGER NOT NULL REFERENCES chats(id),
            tag_id   INTEGER NOT NULL REFERENCES tags(id),
            added_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, tag_id)
        );

        CREATE TABLE chat_actions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      INTEGER NOT NULL REFERENCES chats(id),
            action       TEXT NOT NULL,
            object_kind  TEXT,
            object_id    TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL
        );

        CREATE INDEX idx_inputs_occurred    ON inputs(occurred_at);
        CREATE INDEX idx_inputs_source      ON inputs(source);
        CREATE INDEX idx_tag_notes_tag      ON tag_notes(tag_id);
        CREATE INDEX idx_tag_notes_date     ON tag_notes(note_date);
        CREATE INDEX idx_object_tags_obj    ON object_tags(object_kind, object_id);
        CREATE INDEX idx_object_tags_tag    ON object_tags(tag_id);
        CREATE INDEX idx_obj_hl_obj         ON object_highlights(object_kind, object_id);
        CREATE INDEX idx_chat_messages_chat ON chat_messages(chat_id);
    """)
    conn.commit()
    print("Created app.db schema")

    # -------------------------------------------------------------------------
    # Attach source databases
    # -------------------------------------------------------------------------
    conn.execute(f"ATTACH DATABASE '{ENTRIES_DB}' AS edb")
    conn.execute(f"ATTACH DATABASE '{TOPICS_DB}'  AS tdb")

    # -------------------------------------------------------------------------
    # 1. entry_index → inputs
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT id, date, time FROM edb.entry_index").fetchall()
    print(f"\nMigrating {len(src_rows)} entry_index rows → inputs...")
    for r in src_rows:
        occurred_at = f"{r['date']} {r['time']}"
        conn.execute(
            "INSERT OR IGNORE INTO inputs (id, source, occurred_at, created_at, updated_at) "
            "VALUES (?, 'journal', ?, ?, ?)",
            (r["id"], occurred_at, now_iso, now_iso),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 2. topics → tags (kind='topic', preserve integer PKs)
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT * FROM tdb.topics").fetchall()
    print(f"Migrating {len(src_rows)} topics → tags...")
    for r in src_rows:
        conn.execute(
            "INSERT INTO tags (id, kind, name, description, summary, color, created_at, updated_at) "
            "VALUES (?, 'topic', ?, ?, ?, ?, ?, ?)",
            (r["id"], r["name"], r["description"], r["summary"],
             r["color"], r["created_at"], r["updated_at"]),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 3. topic_entries → tag_notes
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT * FROM tdb.topic_entries").fetchall()
    print(f"Migrating {len(src_rows)} topic_entries → tag_notes...")
    for r in src_rows:
        conn.execute(
            "INSERT INTO tag_notes (id, tag_id, note_date, content, created_at, is_archived) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (r["id"], r["topic_id"], r["journal_date"], r["content"],
             r["created_at"], r["is_archived"]),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 4. entry_topic_tags → object_tags
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT * FROM tdb.entry_topic_tags").fetchall()
    print(f"Migrating {len(src_rows)} entry_topic_tags → object_tags...")
    for r in src_rows:
        conn.execute(
            "INSERT OR IGNORE INTO object_tags "
            "(object_kind, object_id, tag_id, tag_source, created_at) "
            "VALUES ('input', ?, ?, 'intake_inferred', ?)",
            (r["entry_id"], r["topic_id"], now_iso),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 5. entry_highlights → object_highlights
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT * FROM tdb.entry_highlights").fetchall()
    print(f"Migrating {len(src_rows)} entry_highlights → object_highlights...")
    for r in src_rows:
        conn.execute(
            "INSERT INTO object_highlights (id, object_kind, object_id, tag_id, sentence, source) "
            "VALUES (?, 'input', ?, ?, ?, 'intake')",
            (r["id"], r["entry_id"], r["topic_id"], r["sentence"]),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 6. topic_chats → chats (drop topic_id col) + chat_scope_tags
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT * FROM tdb.topic_chats").fetchall()
    print(f"Migrating {len(src_rows)} topic_chats → chats + chat_scope_tags...")
    for r in src_rows:
        conn.execute(
            "INSERT INTO chats (id, title, created_at) VALUES (?, ?, ?)",
            (r["id"], r["title"], r["created_at"]),
        )
        conn.execute(
            "INSERT INTO chat_scope_tags (chat_id, tag_id, added_at) VALUES (?, ?, ?)",
            (r["id"], r["topic_id"], r["created_at"]),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 7. topic_chat_messages → chat_messages
    # -------------------------------------------------------------------------
    src_rows = conn.execute("SELECT * FROM tdb.topic_chat_messages").fetchall()
    print(f"Migrating {len(src_rows)} topic_chat_messages → chat_messages...")
    for r in src_rows:
        conn.execute(
            "INSERT INTO chat_messages (id, chat_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["id"], r["chat_id"], r["role"], r["content"], r["created_at"]),
        )
    conn.commit()

    # -------------------------------------------------------------------------
    # 8. Verify journal_topic_tags is empty (dead code from prior migration)
    # -------------------------------------------------------------------------
    jtt_count = conn.execute("SELECT COUNT(*) FROM tdb.journal_topic_tags").fetchone()[0]
    if jtt_count > 0:
        print(f"\nWARNING: journal_topic_tags has {jtt_count} rows — these were NOT migrated "
              f"(this table was dead code). If you have date-based tags, run the old "
              f"_migrate_date_tags_to_entry_tags() first, then re-run this script.")

    conn.execute("DETACH DATABASE edb")
    conn.execute("DETACH DATABASE tdb")

    # -------------------------------------------------------------------------
    # Verification report
    # -------------------------------------------------------------------------
    print("\n--- Verification row counts ---")
    checks = [
        ("inputs",             "entry_index",          ENTRIES_DB),
        ("tags",               "topics",               TOPICS_DB),
        ("tag_notes",          "topic_entries",        TOPICS_DB),
        ("object_tags",        "entry_topic_tags",     TOPICS_DB),
        ("object_highlights",  "entry_highlights",     TOPICS_DB),
        ("chats",              "topic_chats",          TOPICS_DB),
        ("chat_messages",      "topic_chat_messages",  TOPICS_DB),
    ]
    all_ok = True
    for new_table, old_table, old_db_path in checks:
        new_count = conn.execute(f"SELECT COUNT(*) FROM {new_table}").fetchone()[0]
        old_conn = sqlite3.connect(old_db_path)
        old_count = old_conn.execute(f"SELECT COUNT(*) FROM {old_table}").fetchone()[0]
        old_conn.close()
        status = "OK" if new_count == old_count else "MISMATCH"
        if status != "OK":
            all_ok = False
        print(f"  {status:8s}  {new_table} ({new_count}) vs {old_table} ({old_count})")

    # chat_scope_tags should have one row per topic_chat
    cst_count = conn.execute("SELECT COUNT(*) FROM chat_scope_tags").fetchone()[0]
    chats_count = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    status = "OK" if cst_count == chats_count else "MISMATCH"
    if status != "OK":
        all_ok = False
    print(f"  {status:8s}  chat_scope_tags ({cst_count}) == chats ({chats_count})")

    conn.close()

    if all_ok:
        print("\nMigration complete. All row counts match.")
        print("Next step: update journal.py and topics.py to use app.db, then restart the service.")
    else:
        print("\nMigration FAILED: row count mismatches detected. Do not restart the service.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
