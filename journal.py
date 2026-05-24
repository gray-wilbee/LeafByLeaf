from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import db

JOURNAL_DIR = os.environ.get("JOURNAL_DIR", "/home/will/data/journal")


# ---------------------------------------------------------------------------
# Entry index helpers
# ---------------------------------------------------------------------------

def init_entries_db():
    migrations = [
        # Version 1: Initial schema
        """
        CREATE TABLE IF NOT EXISTS inputs (
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
        CREATE INDEX IF NOT EXISTS idx_inputs_occurred ON inputs(occurred_at);
        CREATE INDEX IF NOT EXISTS idx_inputs_source   ON inputs(source);
        """,
        # Version 2: Multi-user support
        """
        -- Handled in idempotent block below
        """
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)

    # Migrations
    with db.get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(inputs)").fetchall()}
        if "user_id" not in cols:
            conn.execute("ALTER TABLE inputs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        if "title" not in cols:
            conn.execute("ALTER TABLE inputs ADD COLUMN title TEXT")


def _create_entry_id(user_id: int, date_str: str, time_str: str) -> str:
    entry_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    occurred_at = f"{date_str} {time_str}"
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO inputs (id, source, occurred_at, user_id, created_at, updated_at) "
            "VALUES (?, 'journal', ?, ?, ?, ?)",
            (entry_id, occurred_at, user_id, now, now),
        )
        row = conn.execute(
            "SELECT id FROM inputs WHERE user_id=? AND source='journal' AND occurred_at=?",
            (user_id, occurred_at),
        ).fetchone()
    return row["id"]


def lookup_entry_id(user_id: int, date_str: str, time_str: str) -> str | None:
    occurred_at = f"{date_str} {time_str}"
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id FROM inputs WHERE user_id=? AND source='journal' AND occurred_at=?",
            (user_id, occurred_at),
        ).fetchone()
    return row["id"] if row else None


def get_entry_by_id(user_id: int, entry_id: str) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT occurred_at, title FROM inputs WHERE id=? AND user_id=?", (entry_id, user_id)
        ).fetchone()
    if not row:
        return None
    date_str, time_str = row["occurred_at"].split(" ", 1)
    content = get_entry(user_id, date_str, time_str)
    if content is None:
        return None
    return {"id": entry_id, "date": date_str, "time": time_str, "title": row["title"], "content": content}


def get_input_by_id(user_id: int, input_id: str) -> dict | None:
    """Return any input type in the journal stream."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id, source, content, occurred_at, title FROM inputs WHERE id=? AND user_id=?",
            (input_id, user_id),
        ).fetchone()
    if not row:
        return None
    occurred_at = row["occurred_at"]
    date_str, time_str = _split_occurred_at(occurred_at)
    if row["source"] == "journal":
        content = get_entry(user_id, date_str, time_str)
    else:
        content = row["content"]
    if content is None:
        return None
    return {
        "id": row["id"],
        "source": row["source"],
        "date": date_str,
        "time": time_str,
        "occurred_at": occurred_at,
        "title": row["title"],
        "content": content,
    }


def get_entry_ids_for_date(user_id: int, date_str: str) -> list[str]:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM inputs WHERE user_id=? AND source='journal' AND occurred_at LIKE ?",
            (user_id, f"{date_str}%"),
        ).fetchall()
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Markdown file helpers (per-user directories)
# ---------------------------------------------------------------------------

def _user_dir(user_id: int) -> str:
    return os.path.join(JOURNAL_DIR, str(user_id))


def _path(user_id: int, date_str: str) -> str:
    return os.path.join(_user_dir(user_id), f"{date_str}.md")


def _get_day(user_id: int, date_str: str) -> str:
    path = _path(user_id, date_str)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _list_days(user_id: int) -> list:
    d = _user_dir(user_id)
    os.makedirs(d, exist_ok=True)
    files = [f[:-3] for f in os.listdir(d) if f.endswith(".md")]
    return sorted(files, reverse=True)


def _write_day(user_id: int, date_str: str, content: str):
    d = _user_dir(user_id)
    os.makedirs(d, exist_ok=True)
    with open(_path(user_id, date_str), "w", encoding="utf-8") as f:
        f.write(content)


def _parse_day(content: str) -> tuple[str, list[dict]]:
    """Parse a day file into (title_block, [{time, content}])."""
    parts = content.split("\n## ")
    title_block = parts[0].rstrip("\n")
    entries = []
    for part in parts[1:]:
        first_line, _, body = part.partition("\n")
        time_str = first_line.strip()
        body = body.strip().rstrip("-").strip()
        if time_str:
            entries.append({"time": time_str, "content": body})
    return title_block, entries


def _write_day_from_parts(user_id: int, date_str: str, title_block: str, entries: list[dict]):
    entries_sorted = sorted(entries, key=lambda e: e["time"])
    out = title_block + "\n\n"
    for entry in entries_sorted:
        out += f"## {entry['time']}\n\n{entry['content']}\n\n---\n\n"
    _write_day(user_id, date_str, out)


# ---------------------------------------------------------------------------
# Public API — entries are the primary unit, not days
# ---------------------------------------------------------------------------

def append_entry(user_id: int, date_str: str, time_str: str, text: str) -> str:
    """Write entry to markdown and register in inputs table. Returns the entry_id."""
    entry_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    occurred_at = f"{date_str} {time_str}"

    with db.get_db() as conn:
        # 1. Register in DB first
        conn.execute(
            "INSERT OR IGNORE INTO inputs (id, source, occurred_at, user_id, created_at, updated_at, title) "
            "VALUES (?, 'journal', ?, ?, ?, ?, ?)",
            (entry_id, occurred_at, user_id, now, now, None),
        )
        # Ensure we have the correct ID (in case of IGNORE)
        row = conn.execute(
            "SELECT id FROM inputs WHERE user_id=? AND source='journal' AND occurred_at=?",
            (user_id, occurred_at),
        ).fetchone()
        final_id = row["id"]

        # 2. Write to Markdown file
        # If this fails, the DB transaction will roll back.
        d = _user_dir(user_id)
        os.makedirs(d, exist_ok=True)
        path = _path(user_id, date_str)
        is_new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# {date_str}\n\n")
            f.write(f"## {time_str}\n\n{text}\n\n---\n\n")

    return final_id


def get_entry(user_id: int, date_str: str, time_str: str) -> str | None:
    """Return the body of a specific entry, or None if not found."""
    content = _get_day(user_id, date_str)
    if not content:
        return None
    for section in content.split("\n## ")[1:]:
        first_line, _, body = section.partition("\n")
        if first_line.strip() == time_str:
            return body.strip().rstrip("-").strip()
    return None


def list_entries(user_id: int) -> list:
    """Return all journal entries, most recent first.
    Each entry: {id, date, time, content}
    """
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, occurred_at, title FROM inputs "
            "WHERE user_id=? AND source='journal' ORDER BY occurred_at DESC",
            (user_id,),
        ).fetchall()
    entries = []
    for row in rows:
        date_str, time_str = row["occurred_at"].split(" ", 1)
        content = get_entry(user_id, date_str, time_str)
        if content is not None:
            entries.append({"id": row["id"], "date": date_str, "time": time_str, "title": row["title"], "content": content})
    return entries


def _split_occurred_at(occurred_at: str) -> tuple[str, str]:
    if "T" in occurred_at:
        date_str, rest = occurred_at.split("T", 1)
    elif " " in occurred_at:
        date_str, rest = occurred_at.split(" ", 1)
    else:
        return occurred_at[:10], "00:00:00"
    time_str = rest.split("+", 1)[0].split("-", 1)[0].split(".", 1)[0].replace("Z", "")
    return date_str, (time_str or "00:00:00")[:8]


def _occurred_at_sort_sql(alias: str = "i") -> str:
    col = f"{alias}.occurred_at"
    return (
        f"CASE WHEN instr({col}, 'T') > 0 "
        f"THEN datetime(replace(substr({col}, 1, 19), 'T', ' ')) "
        f"ELSE datetime({col}) END"
    )


def list_journal_stream(user_id: int, sources: list[str] | None = None,
                        tag_ids: list[int] | None = None,
                        date_from: str | None = None,
                        date_to: str | None = None) -> list[dict]:
    """Return journal and saved-chat inputs newest-first."""
    sources = sources or ["journal", "chat_summary", "chat_transcript"]
    where = ["i.user_id=?"]
    params: list = [user_id]
    placeholders = ",".join("?" * len(sources))
    where.append(f"i.source IN ({placeholders})")
    params.extend(sources)
    if date_from:
        where.append("i.occurred_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("i.occurred_at <= ?")
        params.append(date_to + " 99:99:99")
    if tag_ids:
        placeholders = ",".join("?" * len(tag_ids))
        where.append(
            f"i.id IN (SELECT object_id FROM object_tags WHERE object_kind='input' AND tag_id IN ({placeholders}))"
        )
        params.extend(tag_ids)

    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT i.id FROM inputs i WHERE {' AND '.join(where)} "
            f"ORDER BY {_occurred_at_sort_sql('i')} DESC, i.id DESC",
            params,
        ).fetchall()

    items = []
    for row in rows:
        item = get_input_by_id(user_id, row["id"])
        if item:
            items.append(item)
    return items


def update_entry(user_id: int, old_date: str, old_time: str, new_date: str, new_time: str, new_content: str):
    """Update an entry's timestamp and/or content. Entry ID is preserved."""
    old_occurred_at = f"{old_date} {old_time}"
    new_occurred_at = f"{new_date} {new_time}"
    now = datetime.now(timezone.utc).isoformat()

    with db.get_db() as conn:
        # 1. Update DB index
        conn.execute(
            "UPDATE inputs SET occurred_at=?, updated_at=? "
            "WHERE user_id=? AND source='journal' AND occurred_at=?",
            (new_occurred_at, now, user_id, old_occurred_at),
        )

        # 2. Update Markdown files
        # If this fails, the DB update rolls back.
        old_raw = _get_day(user_id, old_date)
        if not old_raw:
            return
        title_block, entries = _parse_day(old_raw)
        remaining = [e for e in entries if e["time"] != old_time]

        if old_date == new_date:
            remaining.append({"time": new_time, "content": new_content})
            _write_day_from_parts(user_id, old_date, title_block, remaining)
        else:
            if remaining:
                _write_day_from_parts(user_id, old_date, title_block, remaining)
            else:
                path = _path(user_id, old_date)
                if os.path.exists(path):
                    os.unlink(path)
            new_raw = _get_day(user_id, new_date)
            if new_raw:
                new_title_block, new_entries = _parse_day(new_raw)
            else:
                new_title_block = f"# {new_date}"
                new_entries = []
            new_entries.append({"time": new_time, "content": new_content})
            _write_day_from_parts(user_id, new_date, new_title_block, new_entries)


def has_entries(user_id: int, date_str: str) -> bool:
    """Return True if there are any entries stored for the given date."""
    return bool(_get_day(user_id, date_str))


def create_input(user_id: int, source: str, content: str, occurred_at: str | None = None) -> str:
    """Create a non-journal input (chat_summary, chat_transcript, etc.) with content in DB.
    Returns 8-char hex ID."""
    input_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO inputs (id, source, content, occurred_at, user_id, created_at, updated_at, title) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (input_id, source, content, occurred_at or now, user_id, now, now, None),
        )
    return input_id


def local_occurred_at(dt: datetime, tz_name: str = "America/Chicago") -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def update_entry_title(user_id: int, entry_id: str, title: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    clean = (title or "").strip()[:120] or None
    with db.get_db() as conn:
        conn.execute(
            "UPDATE inputs SET title=?, updated_at=? WHERE id=? AND user_id=?",
            (clean, now, entry_id, user_id),
        )


def fallback_entry_title(content: str) -> str:
    for line in content.splitlines():
        text = line.strip()
        if text.startswith("#"):
            return text.lstrip("#").strip()[:80] or "Journal Entry"
    text = " ".join(content.split())
    if not text:
        return "Journal Entry"
    sentence = text.split(".", 1)[0].strip()
    return (sentence[:77] + "...") if len(sentence) > 80 else sentence


def search_entries(user_id: int, query: str | None = None, date_from: str | None = None,
                   date_to: str | None = None, limit: int = 20) -> list:
    """Search journal entries by content and/or date range.

    Iterates over entries from the DB index and reads content from markdown files.
    Returns list of {id, date, time, preview} dicts.
    """
    with db.get_db() as conn:
        params: list = [user_id]
        where = ["user_id=?", "source='journal'"]
        if date_from:
            where.append("occurred_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("occurred_at <= ?")
            params.append(date_to + " 99:99:99")  # include whole day
        rows = conn.execute(
            f"SELECT id, occurred_at FROM inputs WHERE {' AND '.join(where)} "
            "ORDER BY occurred_at DESC",
            params,
        ).fetchall()

    results = []
    for row in rows:
        date_str, time_str = row["occurred_at"].split(" ", 1)
        content = get_entry(user_id, date_str, time_str)
        if content is None:
            continue
        if query and query.lower() not in content.lower():
            continue
        results.append({
            "id": row["id"],
            "date": date_str,
            "time": time_str,
            "preview": content[:200],
        })
        if len(results) >= limit:
            break
    return results


def get_context(user_id: int, n_recent: int = 7) -> str:
    """Return the most recent n_recent days of raw entry content, oldest first."""
    days = _list_days(user_id)[:n_recent]
    parts = [_get_day(user_id, d) for d in reversed(days) if _get_day(user_id, d)]
    return "\n\n".join(parts)
