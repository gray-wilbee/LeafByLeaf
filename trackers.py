from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
import db


class TrackerError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db():
    with db.get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trackers (
                id                         TEXT PRIMARY KEY,
                user_id                    INTEGER NOT NULL,
                name                       TEXT NOT NULL,
                type                       TEXT NOT NULL DEFAULT 'yes_no',
                frequency                  TEXT NOT NULL DEFAULT 'Daily',
                cron_expression            TEXT,
                capture_instructions       TEXT,
                ai_commentary_instructions TEXT,
                sort_order                 INTEGER NOT NULL DEFAULT 0,
                created_at                 TEXT NOT NULL,
                updated_at                 TEXT NOT NULL,
                archived_at                TEXT
            );

            CREATE TABLE IF NOT EXISTS tracker_entries (
                id          TEXT PRIMARY KEY,
                tracker_id  TEXT NOT NULL REFERENCES trackers(id),
                user_id     INTEGER NOT NULL,
                entry_date  TEXT NOT NULL,
                value_json  TEXT,
                skipped     INTEGER NOT NULL DEFAULT 0,
                source      TEXT NOT NULL DEFAULT 'manual',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(tracker_id, entry_date)
            );

            CREATE TABLE IF NOT EXISTS tracker_commentary (
                id                TEXT PRIMARY KEY,
                tracker_id        TEXT NOT NULL REFERENCES trackers(id),
                user_id           INTEGER NOT NULL,
                commentary        TEXT NOT NULL,
                generated_at      TEXT NOT NULL,
                latest_entry_date TEXT NOT NULL,
                UNIQUE(tracker_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_tracker_entries_tracker
                ON tracker_entries(tracker_id, entry_date);
            CREATE INDEX IF NOT EXISTS idx_tracker_entries_user
                ON tracker_entries(user_id, entry_date);
        """)


# ---------------------------------------------------------------------------
# Trackers CRUD
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r) -> dict | None:
    if r is None:
        return None
    return dict(r)


def create_tracker(
    user_id: int,
    *,
    name: str,
    type: str = "yes_no",
    frequency: str = "Daily",
    cron_expression: str | None = None,
    capture_instructions: str | None = None,
    ai_commentary_instructions: str | None = None,
) -> str:
    if type not in ("yes_no", "number", "text"):
        raise TrackerError(f"invalid type: {type!r}")
    tid = uuid.uuid4().hex[:8]
    now = _now()
    # Assign sort_order as max + 1 for this user
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next FROM trackers WHERE user_id=? AND archived_at IS NULL",
            (user_id,),
        ).fetchone()
        next_order = row["next"] if row else 0
        conn.execute(
            """INSERT INTO trackers
               (id, user_id, name, type, frequency, cron_expression,
                capture_instructions, ai_commentary_instructions,
                sort_order, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, user_id, name, type, frequency, cron_expression,
             capture_instructions, ai_commentary_instructions,
             next_order, now, now),
        )
    return tid


def get_tracker(user_id: int, tracker_id: str) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT * FROM trackers WHERE id=? AND user_id=? AND archived_at IS NULL",
            (tracker_id, user_id),
        ).fetchone()
    return _row(r)


def list_trackers(user_id: int, include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM trackers WHERE user_id=?"
    args: list = [user_id]
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY sort_order ASC, created_at ASC"
    with db.get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def update_tracker(user_id: int, tracker_id: str, **fields) -> None:
    allowed = {
        "name", "type", "frequency", "cron_expression",
        "capture_instructions", "ai_commentary_instructions",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "type" in updates and updates["type"] not in ("yes_no", "number", "text"):
        raise TrackerError(f"invalid type: {updates['type']!r}")
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [tracker_id, user_id]
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE trackers SET {set_clause} WHERE id=? AND user_id=?",
            vals,
        )


def archive_tracker(user_id: int, tracker_id: str) -> None:
    now = _now()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE trackers SET archived_at=?, updated_at=? WHERE id=? AND user_id=?",
            (now, now, tracker_id, user_id),
        )


def reorder_trackers(user_id: int, order: list[dict]) -> None:
    """order: [{id, sort_order}, ...]"""
    now = _now()
    with db.get_db() as conn:
        for item in order:
            conn.execute(
                "UPDATE trackers SET sort_order=?, updated_at=? WHERE id=? AND user_id=?",
                (item["sort_order"], now, item["id"], user_id),
            )


# ---------------------------------------------------------------------------
# Entries CRUD
# ---------------------------------------------------------------------------

def get_entry(user_id: int, entry_id: str) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT * FROM tracker_entries WHERE id=? AND user_id=?",
            (entry_id, user_id),
        ).fetchone()
    return _row(r)


def get_entry_by_date(tracker_id: str, entry_date: str) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT * FROM tracker_entries WHERE tracker_id=? AND entry_date=?",
            (tracker_id, entry_date),
        ).fetchone()
    return _row(r)


def list_entries(
    user_id: int,
    tracker_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM tracker_entries WHERE tracker_id=? AND user_id=?"
    args: list = [tracker_id, user_id]
    if date_from:
        sql += " AND entry_date >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND entry_date <= ?"
        args.append(date_to)
    sql += " ORDER BY entry_date DESC LIMIT ?"
    args.append(limit)
    with db.get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def upsert_entry(
    user_id: int,
    tracker_id: str,
    entry_date: str,
    value_json: str | None,
    *,
    source: str = "manual",
    skipped: bool = False,
) -> str:
    """Insert or update an entry. Returns the entry id."""
    now = _now()
    existing = get_entry_by_date(tracker_id, entry_date)
    if existing:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE tracker_entries SET value_json=?, source=?, skipped=?, updated_at=? WHERE id=?",
                (value_json, source, int(skipped), now, existing["id"]),
            )
        return existing["id"]
    eid = uuid.uuid4().hex[:12]
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO tracker_entries
               (id, tracker_id, user_id, entry_date, value_json, skipped, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (eid, tracker_id, user_id, entry_date, value_json, int(skipped), source, now, now),
        )
    return eid


def update_entry(user_id: int, entry_id: str, value_json: str | None) -> None:
    now = _now()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tracker_entries SET value_json=?, source='manual', updated_at=? WHERE id=? AND user_id=?",
            (value_json, now, entry_id, user_id),
        )


def skip_entry(user_id: int, entry_id: str) -> None:
    now = _now()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tracker_entries SET skipped=1, updated_at=? WHERE id=? AND user_id=?",
            (now, entry_id, user_id),
        )


# ---------------------------------------------------------------------------
# Snapshots (pending entries)
# ---------------------------------------------------------------------------

def list_pending_snapshots(user_id: int, today: str) -> list[dict]:
    """Return entries where value is null and not skipped, up to today, with tracker info."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT e.*, t.name AS tracker_name, t.type AS tracker_type
               FROM tracker_entries e
               JOIN trackers t ON e.tracker_id = t.id
               WHERE e.user_id=?
                 AND e.value_json IS NULL
                 AND e.skipped = 0
                 AND e.entry_date <= ?
                 AND t.archived_at IS NULL
               ORDER BY e.entry_date ASC, t.sort_order ASC""",
            (user_id, today),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# AI Commentary
# ---------------------------------------------------------------------------

def get_commentary(user_id: int, tracker_id: str) -> dict | None:
    with db.get_db() as conn:
        r = conn.execute(
            "SELECT * FROM tracker_commentary WHERE tracker_id=? AND user_id=?",
            (tracker_id, user_id),
        ).fetchone()
    return _row(r)


def upsert_commentary(
    user_id: int,
    tracker_id: str,
    commentary: str,
    latest_entry_date: str,
) -> None:
    now = _now()
    cid = uuid.uuid4().hex[:12]
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO tracker_commentary
               (id, tracker_id, user_id, commentary, generated_at, latest_entry_date)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(tracker_id, user_id)
               DO UPDATE SET commentary=excluded.commentary,
                             generated_at=excluded.generated_at,
                             latest_entry_date=excluded.latest_entry_date""",
            (cid, tracker_id, user_id, commentary, now, latest_entry_date),
        )


def commentary_is_stale(tracker_id: str, user_id: int) -> bool:
    """Returns True if there are entries newer than the last commentary generation."""
    comment = get_commentary(user_id, tracker_id)
    if not comment:
        return True
    with db.get_db() as conn:
        r = conn.execute(
            """SELECT 1 FROM tracker_entries
               WHERE tracker_id=? AND user_id=?
                 AND value_json IS NOT NULL
                 AND entry_date > ?
               LIMIT 1""",
            (tracker_id, user_id, comment["latest_entry_date"]),
        ).fetchone()
    return r is not None


# ---------------------------------------------------------------------------
# Cron / scheduling helpers
# ---------------------------------------------------------------------------

def list_trackers_due(user_id: int, as_of_date: str) -> list[dict]:
    """Return active trackers that don't have an entry for as_of_date yet."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT t.* FROM trackers t
               WHERE t.user_id=? AND t.archived_at IS NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM tracker_entries e
                     WHERE e.tracker_id = t.id
                       AND e.entry_date = ?
                 )""",
            (user_id, as_of_date),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_entry_date(tracker_id: str, before_date: str | None = None) -> str | None:
    sql = "SELECT MAX(entry_date) AS d FROM tracker_entries WHERE tracker_id=? AND value_json IS NOT NULL"
    args: list = [tracker_id]
    if before_date:
        sql += " AND entry_date < ?"
        args.append(before_date)
    with db.get_db() as conn:
        r = conn.execute(sql, args).fetchone()
    return r["d"] if r else None
