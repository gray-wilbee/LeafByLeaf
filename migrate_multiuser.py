"""One-time migration: add multi-user support.

Run on the server ONCE before deploying the multi-user code:
    python migrate_multiuser.py

What it does:
1. Creates the `users` table and seeds the existing user (wilbee)
2. Adds `user_id` column (default 1) to all data tables
3. Recreates tables that need constraint changes (tags, settings, inputs)
4. Moves markdown journal files into a per-user subdirectory
5. Adds indexes on user_id columns

Safe to re-run: checks for existing columns/tables before modifying.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

APP_DB = os.path.join(os.path.dirname(__file__), "app.db")
LOGS_DB = os.path.join(os.path.dirname(__file__), "logs.db")
JOURNAL_DIR = os.environ.get("JOURNAL_DIR", "/home/will/data/journal")

# Read legacy env vars for seeding
PASSWORD_HASH = os.environ.get("PASSWORD_HASH", "")
UPLOAD_API_KEY = os.environ.get("UPLOAD_API_KEY", "")


def _has_column(conn, table, column):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def migrate_app_db():
    conn = sqlite3.connect(APP_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # off during migration

    # --- Step 1: Create users table & seed wilbee ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            api_key       TEXT UNIQUE,
            created_at    TEXT NOT NULL
        )
    """)

    existing = conn.execute("SELECT id FROM users WHERE username='wilbee'").fetchone()
    if not existing:
        if not PASSWORD_HASH:
            print("ERROR: PASSWORD_HASH env var required to seed wilbee user.")
            print("Set it to the existing bcrypt hash, e.g.:")
            print('  PASSWORD_HASH="pbkdf2:sha256:..." python migrate_multiuser.py')
            sys.exit(1)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO users (id, username, password_hash, api_key, created_at) "
            "VALUES (1, 'wilbee', ?, ?, ?)",
            (PASSWORD_HASH, UPLOAD_API_KEY or None, now),
        )
        print("Seeded user: wilbee (id=1)")
    else:
        print("User wilbee already exists, skipping seed.")

    # --- Step 2: Add user_id to simple tables (no constraint changes) ---
    simple_tables = [
        "tag_notes", "tag_links", "object_tags", "object_highlights",
        "chats", "tasks", "task_links", "task_sources",
    ]
    for table in simple_tables:
        if not _table_exists(conn, table):
            print(f"  Table {table} does not exist, skipping.")
            continue
        if _has_column(conn, table, "user_id"):
            print(f"  {table}.user_id already exists, skipping.")
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user_id ON {table}(user_id)")
        print(f"  Added user_id to {table}")

    # --- Step 3: Recreate `tags` table (UNIQUE constraint change) ---
    if _has_column(conn, "tags", "user_id"):
        print("  tags.user_id already exists, skipping recreate.")
    else:
        print("  Recreating tags table with user_id + UNIQUE(user_id, kind, name)...")
        conn.executescript("""
            ALTER TABLE tags RENAME TO _tags_old;

            CREATE TABLE tags (
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

            INSERT INTO tags (id, kind, name, description, summary, color, keywords,
                              embedding, parent_tag_id, user_id, created_at, updated_at)
            SELECT id, kind, name, description, summary, color, keywords,
                   embedding, parent_tag_id, 1, created_at, updated_at
            FROM _tags_old;

            DROP TABLE _tags_old;
            CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id);
        """)
        print("  Done recreating tags.")

    # --- Step 4: Recreate `settings` table (PK change) ---
    if _has_column(conn, "settings", "user_id"):
        print("  settings.user_id already exists, skipping recreate.")
    else:
        print("  Recreating settings table with composite PK (user_id, key)...")
        conn.executescript("""
            ALTER TABLE settings RENAME TO _settings_old;

            CREATE TABLE settings (
                user_id    INTEGER NOT NULL DEFAULT 1,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            );

            INSERT INTO settings (user_id, key, value, updated_at)
            SELECT 1, key, value, updated_at FROM _settings_old;

            DROP TABLE _settings_old;
        """)
        print("  Done recreating settings.")

    # --- Step 5: Recreate `inputs` table (UNIQUE constraint change) ---
    if _has_column(conn, "inputs", "user_id"):
        print("  inputs.user_id already exists, skipping recreate.")
    else:
        print("  Recreating inputs table with user_id + UNIQUE(user_id, source, occurred_at)...")
        conn.executescript("""
            ALTER TABLE inputs RENAME TO _inputs_old;

            CREATE TABLE inputs (
                id                TEXT PRIMARY KEY,
                source            TEXT NOT NULL DEFAULT 'journal',
                content           TEXT,
                occurred_at       TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                source_session_id TEXT,
                user_id           INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, source, occurred_at)
            );

            INSERT INTO inputs (id, source, content, occurred_at, created_at,
                                updated_at, source_session_id, user_id)
            SELECT id, source, content, occurred_at, created_at,
                   updated_at, source_session_id, 1
            FROM _inputs_old;

            DROP TABLE _inputs_old;
            CREATE INDEX IF NOT EXISTS idx_inputs_occurred ON inputs(occurred_at);
            CREATE INDEX IF NOT EXISTS idx_inputs_source ON inputs(source);
            CREATE INDEX IF NOT EXISTS idx_inputs_user_id ON inputs(user_id);
        """)
        print("  Done recreating inputs.")

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()
    print("app.db migration complete.")


def migrate_logs_db():
    if not os.path.exists(LOGS_DB):
        print("logs.db not found, skipping.")
        return
    conn = sqlite3.connect(LOGS_DB)
    conn.execute("PRAGMA journal_mode=WAL")

    if _has_column(conn, "upload_logs", "user_id"):
        print("  upload_logs.user_id already exists, skipping.")
    else:
        conn.execute("ALTER TABLE upload_logs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_logs_user_id ON upload_logs(user_id)")
        print("  Added user_id to upload_logs")

    conn.commit()
    conn.close()
    print("logs.db migration complete.")


def migrate_journal_files():
    """Move JOURNAL_DIR/*.md into JOURNAL_DIR/1/ for user_id=1."""
    user_dir = os.path.join(JOURNAL_DIR, "1")
    if os.path.exists(user_dir):
        print(f"  {user_dir} already exists, skipping file migration.")
        return
    if not os.path.exists(JOURNAL_DIR):
        print(f"  {JOURNAL_DIR} does not exist, skipping file migration.")
        return

    md_files = [f for f in os.listdir(JOURNAL_DIR) if f.endswith(".md")]
    if not md_files:
        print("  No markdown files to migrate.")
        return

    os.makedirs(user_dir, exist_ok=True)
    for f in md_files:
        src = os.path.join(JOURNAL_DIR, f)
        dst = os.path.join(user_dir, f)
        shutil.move(src, dst)
    print(f"  Moved {len(md_files)} markdown files to {user_dir}")


if __name__ == "__main__":
    print("=== Multi-user migration ===")
    print(f"APP_DB: {APP_DB}")
    print(f"LOGS_DB: {LOGS_DB}")
    print(f"JOURNAL_DIR: {JOURNAL_DIR}")
    print()
    migrate_app_db()
    print()
    migrate_logs_db()
    print()
    migrate_journal_files()
    print()
    print("Migration complete!")
