import os
import sqlite3
from contextlib import contextmanager

DB_DIR = os.path.dirname(__file__)
APP_DB_PATH = os.path.join(DB_DIR, "app.db")
LOGS_DB_PATH = os.path.join(DB_DIR, "logs.db")

@contextmanager
def get_db(path=APP_DB_PATH):
    """
    Context manager for SQLite connections.
    Ensures the connection is closed and pragmas are set.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations(path, migrations):
    """
    Apply a list of migration scripts using PRAGMA user_version.
    migrations: list of SQL strings. Version 1 is migrations[0], etc.

    VoiceJournal keeps several logical module schemas in one SQLite file. The
    global SQLite user_version cannot track those modules independently, so
    scripts must be idempotent and are executed on each init call.
    """
    with get_db(path) as conn:
        for i, script in enumerate(migrations):
            if script.strip():
                conn.executescript(script)
            new_v = i + 1
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            if current_version < new_v:
                conn.execute(f"PRAGMA user_version = {new_v}")
