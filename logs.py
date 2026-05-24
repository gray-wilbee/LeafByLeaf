from __future__ import annotations

from datetime import datetime, timezone

import db

def init_db():
    migrations = [
        # Version 1: Initial schema
        """
        CREATE TABLE IF NOT EXISTS upload_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT NOT NULL UNIQUE,
            received_at TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            date        TEXT,
            time        TEXT,
            words       INTEGER,
            error       TEXT,
            entry_id    TEXT,
            viewed      INTEGER NOT NULL DEFAULT 0,
            user_id     INTEGER NOT NULL DEFAULT 1
        );
        """
    ]
    db.run_migrations(db.LOGS_DB_PATH, migrations)

    # Migrations for existing tables
    with db.get_db(db.LOGS_DB_PATH) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(upload_logs)").fetchall()]
        if "viewed" not in cols:
            conn.execute("ALTER TABLE upload_logs ADD COLUMN viewed INTEGER NOT NULL DEFAULT 0")
        if "entry_id" not in cols:
            conn.execute("ALTER TABLE upload_logs ADD COLUMN entry_id TEXT")
        if "user_id" not in cols:
            conn.execute("ALTER TABLE upload_logs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")


def log_received(user_id: int, job_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db(db.LOGS_DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO upload_logs (job_id, received_at, status, user_id) VALUES (?, ?, 'pending', ?)",
            (job_id, now, user_id),
        )


def log_update(job_id: str, status: str, date: str = None, time: str = None,
               words: int = None, error: str = None, entry_id: str = None) -> None:
    with db.get_db(db.LOGS_DB_PATH) as conn:
        conn.execute(
            """UPDATE upload_logs
               SET status=?, date=?, time=?, words=?, error=?, entry_id=?
               WHERE job_id=?""",
            (status, date, time, words, error, entry_id, job_id),
        )


def mark_all_viewed(user_id: int) -> None:
    """Mark all error logs as viewed (clears the nav badge)."""
    with db.get_db(db.LOGS_DB_PATH) as conn:
        conn.execute(
            "UPDATE upload_logs SET viewed=1 WHERE user_id=? AND status='error' AND viewed=0",
            (user_id,),
        )


def list_logs(user_id: int, limit: int = 100) -> list[dict]:
    with db.get_db(db.LOGS_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT * FROM upload_logs WHERE user_id=? ORDER BY received_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def failed_count(user_id: int) -> int:
    """Count errors that haven't been viewed yet (drives the nav badge)."""
    with db.get_db(db.LOGS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM upload_logs WHERE user_id=? AND status='error' AND viewed=0",
            (user_id,),
        ).fetchone()
    return row[0]


def get_log_by_job_id(job_id: str) -> dict | None:
    """Retrieve a specific job log by its UUID."""
    with db.get_db(db.LOGS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM upload_logs WHERE job_id=?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def list_logs_admin(user_id: int, limit: int = 200) -> list[dict]:
    """Privacy-safe admin log listing. Never returns entry_id."""
    with db.get_db(db.LOGS_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, job_id, received_at, status, date, time, words, error
            FROM upload_logs
            WHERE user_id=?
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def user_log_stats(user_id: int, start: str | None = None, end: str | None = None) -> dict:
    where = ["user_id=?"]
    params: list = [user_id]
    if start:
        where.append("received_at >= ?")
        params.append(start)
    if end:
        where.append("received_at <= ?")
        params.append(end)
    with db.get_db(db.LOGS_DB_PATH) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS uploads,
                   SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   SUM(COALESCE(words, 0)) AS words
            FROM upload_logs
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchone()
    return {
        "uploads": int(row["uploads"] or 0),
        "completed": int(row["completed"] or 0),
        "errors": int(row["errors"] or 0),
        "words": int(row["words"] or 0),
    }
