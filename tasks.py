from __future__ import annotations

import uuid
from datetime import datetime, timezone
import db

STATUS_CYCLE = ["open", "waiting", "done", "cancelled"]
VALID_STATUSES = {"open", "waiting", "done", "cancelled", "suggested"}
PRIORITY_ORDER = {"high": 1, "medium": 2, "low": 3}


def init_db():
    migrations = [
        # Version 1: Initial schema
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id                TEXT PRIMARY KEY,
            title             TEXT NOT NULL,
            description       TEXT,
            status            TEXT NOT NULL DEFAULT 'open',
            waiting_reason    TEXT,
            due_at            TEXT,
            emoji             TEXT,
            priority          TEXT NOT NULL DEFAULT 'medium',
            user_id           INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            completed_at      TEXT,
            source            TEXT NOT NULL DEFAULT 'scan',
            source_session_id TEXT
        );

        CREATE TABLE IF NOT EXISTS task_links (
            from_task_id TEXT NOT NULL REFERENCES tasks(id),
            to_task_id   TEXT NOT NULL REFERENCES tasks(id),
            kind         TEXT NOT NULL,
            sort_order   INTEGER,
            user_id      INTEGER NOT NULL DEFAULT 1,
            source       TEXT NOT NULL DEFAULT 'user',
            PRIMARY KEY (from_task_id, to_task_id, kind)
        );

        CREATE TABLE IF NOT EXISTS task_sources (
            task_id  TEXT NOT NULL REFERENCES tasks(id),
            input_id TEXT NOT NULL,
            user_id  INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (task_id, input_id)
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_created  ON tasks(created_at);
        CREATE INDEX IF NOT EXISTS idx_tl_from        ON task_links(from_task_id);
        CREATE INDEX IF NOT EXISTS idx_tl_to          ON task_links(to_task_id);
        CREATE INDEX IF NOT EXISTS idx_ts_input       ON task_sources(input_id);
        """,
        # Version 2: Multi-user support and metadata columns (emoji, priority, user_id)
        ""
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)

    # Idempotent column additions for existing installations
    with db.get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "emoji" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN emoji TEXT")
        if "priority" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
        if "user_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        if "recurrence_rule" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN recurrence_rule TEXT")
        if "recurrence_source_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN recurrence_source_id TEXT")
        if "soft_deleted" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN soft_deleted INTEGER NOT NULL DEFAULT 0")

        for table in ["task_links", "task_sources"]:
            tc = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "user_id" not in tc:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

def create_task(user_id, title, description=None, status="open", source="scan",
                source_session_id=None, emoji=None, priority="medium", due_at=None,
                recurrence_rule=None, recurrence_source_id=None):
    """Create a task and return its 8-char hex ID."""
    task_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, description, status, emoji, priority, due_at, "
            "user_id, created_at, updated_at, source, source_session_id, recurrence_rule, recurrence_source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, title, description, status, emoji, priority, due_at,
             user_id, now, now, source, source_session_id, recurrence_rule, recurrence_source_id),
        )
    return task_id


def get_task(user_id, task_id):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        return dict(row) if row else None


def update_task(user_id, task_id, **fields):
    allowed = {k: v for k, v in fields.items()
               if k in ("title", "description", "status", "waiting_reason",
                        "due_at", "emoji", "priority", "recurrence_rule")}
    if not allowed:
        return
    now = datetime.now(timezone.utc).isoformat()
    allowed["updated_at"] = now
    if "status" in allowed and allowed["status"] in ("done", "cancelled"):
        allowed.setdefault("completed_at", now)
    elif "status" in allowed and allowed["status"] in ("open", "waiting", "suggested"):
        allowed["completed_at"] = None
    sets = ", ".join(f"{k}=?" for k in allowed)
    vals = list(allowed.values()) + [task_id, user_id]
    with db.get_db() as conn:
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=? AND user_id=?", vals)


def delete_task(user_id, task_id):
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM task_links WHERE from_task_id=? OR to_task_id=?", (task_id, task_id))
        conn.execute("DELETE FROM task_sources WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))


def list_tasks(user_id, status=None):
    """Return all tasks, optionally filtered by status, newest first.
    Suggested tasks are excluded unless status='suggested' is explicitly requested."""
    with db.get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND status=? ORDER BY created_at DESC",
                (user_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND status != 'suggested' ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def list_active_for_dedupe(user_id):
    """Return active + suggested tasks used by extraction dedupe.
    Suggested tasks are included so the same concept isn't re-queued."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, description, due_at, priority, status
            FROM tasks
            WHERE user_id=? AND status NOT IN ('done','cancelled') AND COALESCE(soft_deleted, 0)=0
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_tasks_sorted(user_id, show_done=False, show_cancelled=False,
                      sort_by="default", tag_ids=None):
    """Return tasks with configurable sort/filter. Suggested tasks are always excluded."""
    where_clauses = ["t.user_id = ?", "COALESCE(t.soft_deleted, 0) = 0",
                     "t.status != 'suggested'"]
    params = [user_id]
    if not show_done:
        where_clauses.append("t.status != 'done'")
    if not show_cancelled:
        where_clauses.append("t.status != 'cancelled'")
    if tag_ids:
        import topics
        expanded = set(tag_ids)
        for tid in tag_ids:
            expanded.update(topics.get_descendants(tid))
        tag_ids = list(expanded)
        placeholders = ",".join("?" * len(tag_ids))
        where_clauses.append(
            f"t.id IN (SELECT object_id FROM object_tags WHERE object_kind='task' AND tag_id IN ({placeholders}))"
        )
        params.extend(tag_ids)
    where_sql = "WHERE " + " AND ".join(where_clauses)
    priority_case = "CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 2 END"
    due_null_last = "CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END"
    if sort_by == "priority":
        order_sql = f"ORDER BY {priority_case}, {due_null_last}, t.due_at ASC, t.created_at ASC"
    elif sort_by == "created":
        order_sql = "ORDER BY t.created_at DESC"
    else:
        order_sql = f"ORDER BY {due_null_last}, t.due_at ASC, {priority_case}, t.created_at ASC"
    with db.get_db() as conn:
        rows = conn.execute(f"SELECT t.* FROM tasks t {where_sql} {order_sql}", params).fetchall()
        return [dict(r) for r in rows]


def link_tasks(user_id, from_task_id, to_task_id, kind="part_of", sort_order=None, source="user"):
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO task_links (from_task_id, to_task_id, kind, sort_order, user_id, source) "
            "VALUES (?,?,?,?,?,?)", (from_task_id, to_task_id, kind, sort_order, user_id, source)
        )


def unlink_tasks(from_task_id, to_task_id, kind):
    with db.get_db() as conn:
        conn.execute("DELETE FROM task_links WHERE from_task_id=? AND to_task_id=? AND kind=?", (from_task_id, to_task_id, kind))


def get_children(task_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.* FROM tasks t JOIN task_links tl ON tl.from_task_id = t.id "
            "WHERE tl.to_task_id=? AND tl.kind='part_of' ORDER BY tl.sort_order ASC, t.created_at ASC",
            (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_depends_on(task_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.* FROM tasks t JOIN task_links tl ON tl.to_task_id = t.id "
            "WHERE tl.from_task_id=? AND tl.kind='depends_on' ORDER BY t.created_at ASC",
            (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_parent_ids(user_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT tl.from_task_id FROM task_links tl JOIN tasks t ON t.id = tl.from_task_id "
            "WHERE tl.kind='part_of' AND t.user_id=?", (user_id,)
        ).fetchall()
        return {r["from_task_id"] for r in rows}


def search_tasks_all_statuses(user_id, query: str, limit: int = 6) -> list:
    """Search tasks across all statuses (for global search palette)."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, emoji, status FROM tasks "
            "WHERE user_id=? AND COALESCE(soft_deleted,0)=0 AND title LIKE ? LIMIT ?",
            (user_id, f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_tasks(user_id, query, exclude_id=None, limit=10):
    params = [user_id, f"%{query}%"]
    exclude_sql = f"AND id != ?" if exclude_id else ""
    if exclude_id: params.append(exclude_id)
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT id, title, emoji, status FROM tasks WHERE user_id=? AND title LIKE ? AND status NOT IN ('done','cancelled') {exclude_sql} LIMIT ?",
            params + [limit]
        ).fetchall()
        return [dict(r) for r in rows]


def link_input(user_id, task_id, input_id):
    with db.get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO task_sources (task_id, input_id, user_id) VALUES (?,?,?)", (task_id, input_id, user_id))


def get_tasks_for_input(input_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.* FROM tasks t JOIN task_sources ts ON ts.task_id = t.id "
            "WHERE ts.input_id=? AND COALESCE(t.soft_deleted, 0)=0 ORDER BY t.created_at ASC",
            (input_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_tasks_for_tags(user_id, tag_ids, show_done=True, show_cancelled=True, sort_by="default"):
    return list_tasks_sorted(
        user_id,
        show_done=show_done,
        show_cancelled=show_cancelled,
        sort_by=sort_by,
        tag_ids=tag_ids,
    )


def bulk_update_status(user_id: int, task_ids: list, status: str) -> int:
    if not task_ids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(task_ids))
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE tasks SET status=?, updated_at=? WHERE user_id=? AND id IN ({placeholders})",
            [status, now, user_id] + list(task_ids),
        )
    return len(task_ids)


def bulk_delete(user_id: int, task_ids: list) -> int:
    if not task_ids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(task_ids))
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE tasks SET soft_deleted=1, updated_at=? WHERE user_id=? AND id IN ({placeholders})",
            [now, user_id] + list(task_ids),
        )
    return len(task_ids)


def get_task_tag_map(user_id: int, task_ids: list) -> dict:
    """Return {task_id: [tag_dicts]} for the given task IDs."""
    if not task_ids:
        return {}
    with db.get_db() as conn:
        placeholders = ",".join("?" * len(task_ids))
        rows = conn.execute(
            f"SELECT ot.object_id AS task_id, t.id, t.name, t.kind, t.color "
            f"FROM object_tags ot "
            f"JOIN tags t ON t.id = ot.tag_id "
            f"WHERE ot.object_kind = 'task' AND ot.object_id IN ({placeholders}) AND t.user_id = ? "
            f"ORDER BY t.kind, t.name",
            task_ids + [user_id],
        ).fetchall()
    result = {}
    for r in rows:
        r = dict(r)
        tid = r.pop("task_id")
        result.setdefault(tid, []).append(r)
    return result


def soft_delete_task(user_id, task_id):
    with db.get_db() as conn:
        conn.execute("UPDATE tasks SET soft_deleted=1, updated_at=? WHERE id=? AND user_id=?",
                     (datetime.now(timezone.utc).isoformat(), task_id, user_id))


def restore_task(user_id, task_id):
    with db.get_db() as conn:
        conn.execute("UPDATE tasks SET soft_deleted=0, updated_at=? WHERE id=? AND user_id=?",
                     (datetime.now(timezone.utc).isoformat(), task_id, user_id))


def create_next_occurrence(user_id, source_task, next_due_at):
    import topics
    new_id = create_task(
        user_id,
        title=source_task["title"],
        description=source_task.get("description"),
        emoji=source_task.get("emoji"),
        priority=source_task.get("priority", "medium"),
        due_at=next_due_at,
        recurrence_rule=source_task.get("recurrence_rule"),
        recurrence_source_id=source_task["id"],
        source="recurrence",
    )
    source_tags = topics.get_tags_for_task(source_task["id"])
    if source_tags:
        topics.tag_task(user_id, new_id, [t["id"] for t in source_tags], tag_source="recurrence")
    return new_id


def count_recurring_completions(user_id, root_task_id, period_start, period_end):
    """Count completed occurrences in a recurring task chain for an ISO date range."""
    with db.get_db() as conn:
        row = conn.execute(
            """
            WITH RECURSIVE chain(id) AS (
                SELECT id FROM tasks WHERE id=? AND user_id=?
                UNION ALL
                SELECT t.id
                FROM tasks t
                JOIN chain c ON t.recurrence_source_id = c.id
                WHERE t.user_id=?
            )
            SELECT COUNT(*) AS cnt
            FROM tasks
            WHERE id IN (SELECT id FROM chain)
              AND user_id=?
              AND status='done'
              AND completed_at IS NOT NULL
              AND substr(completed_at, 1, 10) >= ?
              AND substr(completed_at, 1, 10) <= ?
            """,
            (root_task_id, user_id, user_id, user_id, period_start, period_end),
        ).fetchone()
    return int(row["cnt"] or 0)


def list_recurring_task_options(user_id):
    """Return recurring tasks that can be linked to tracker fields."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, status, due_at, recurrence_rule, recurrence_source_id
            FROM tasks
            WHERE user_id=?
              AND COALESCE(soft_deleted,0)=0
              AND recurrence_rule IS NOT NULL
              AND recurrence_rule != ''
            ORDER BY COALESCE(due_at, created_at) DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_suggested_tasks(user_id):
    """Return all suggested tasks with their source entry info, oldest first."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT t.*, ts.input_id AS source_entry_id
            FROM tasks t
            LEFT JOIN task_sources ts ON ts.task_id = t.id
            WHERE t.user_id=? AND t.status='suggested' AND COALESCE(t.soft_deleted,0)=0
            ORDER BY t.created_at ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_suggested_tasks(user_id) -> int:
    """Return count of pending suggested tasks (for nav badge)."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE user_id=? AND status='suggested' AND COALESCE(soft_deleted,0)=0",
            (user_id,),
        ).fetchone()
    return int(row["cnt"] or 0)


def list_today_tasks(user_id, today: str, week_end: str, tag_ids=None):
    """Return open tasks for the Today view, sorted by urgency.
    today and week_end are ISO date strings (YYYY-MM-DD).
    Returns dict with keys: must, should, tiny_wins, rest."""
    where_clauses = ["user_id=?", "status='open'", "COALESCE(soft_deleted,0)=0"]
    where_params: list = [user_id]
    if tag_ids:
        import topics as topics_mod
        expanded = set(tag_ids)
        for tid in tag_ids:
            expanded.update(topics_mod.get_descendants(tid))
        expanded_list = list(expanded)
        placeholders = ",".join("?" * len(expanded_list))
        where_clauses.append(
            f"id IN (SELECT object_id FROM object_tags WHERE object_kind='task' AND tag_id IN ({placeholders}))"
        )
        where_params.extend(expanded_list)
    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            WHERE {" AND ".join(where_clauses)}
            ORDER BY
                CASE WHEN due_at IS NOT NULL AND due_at <= ? THEN 0 ELSE 1 END,
                CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 2 END,
                due_at ASC NULLS LAST,
                created_at ASC
            """,
            where_params + [today],
        ).fetchall()
    all_tasks = [dict(r) for r in rows]
    must, should, tiny_wins, rest = [], [], [], []
    for t in all_tasks:
        due = t.get("due_at")
        pri = t.get("priority", "medium")
        if due and due <= today:
            must.append(t)
        elif pri == "high" or (due and due <= week_end):
            should.append(t)
        elif pri == "low" and not due:
            tiny_wins.append(t)
        else:
            rest.append(t)
    return {"must": must, "should": should, "tiny_wins": tiny_wins, "rest": rest}


def count_tasks(user_id, start: str | None = None, end: str | None = None) -> int:
    where = ["user_id=?"]
    params: list = [user_id]
    if start:
        where.append("created_at >= ?")
        params.append(start)
    if end:
        where.append("created_at <= ?")
        params.append(end)
    with db.get_db() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM tasks WHERE {' AND '.join(where)}", params).fetchone()
    return int(row["cnt"] or 0)
