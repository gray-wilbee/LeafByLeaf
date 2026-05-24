from __future__ import annotations

import json
import uuid
import calendar
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import db

VALID_CADENCES = {"daily", "weekly", "monthly", "ad_hoc"}
VALID_FIELD_TYPES = {"boolean", "number", "text", "scale", "time", "duration", "select", "multi_select"}
VALID_INFERENCE_POLICIES = {"manual_only", "infer_when_explicit", "infer_when_likely", "system_computed", "ask_if_missing"}
VALID_ROW_STATUSES = {"empty", "incomplete", "inferred", "confirmed", "manually_edited"}
VALID_VALUE_SOURCES = {"manual", "inferred", "system", "chat", "mcp", "agent"}
VALID_CONFIDENCES = {None, "low", "medium", "high", "user_confirmed"}


class TrackerValidationError(ValueError):
    pass


def _validate_choice(name, value, valid):
    if value not in valid:
        raise TrackerValidationError(f"invalid {name}: {value}")


def derive_period_bounds(date_str, cadence):
    _validate_choice("cadence", cadence, VALID_CADENCES)
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if cadence == "weekly":
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=6)
    elif cadence == "monthly":
        start = d.replace(day=1)
        end = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    else:
        start = end = d
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def validate_tracker_fields(fields: dict, partial=False):
    if (not partial or "cadence" in fields) and fields.get("cadence") is not None:
        _validate_choice("cadence", fields.get("cadence"), VALID_CADENCES)
    if "timezone" in fields and fields.get("timezone"):
        try:
            ZoneInfo(fields["timezone"])
        except ZoneInfoNotFoundError:
            raise TrackerValidationError("invalid timezone")


def validate_field_fields(fields: dict, partial=False):
    if (not partial or "field_type" in fields) and fields.get("field_type") is not None:
        _validate_choice("field_type", fields.get("field_type"), VALID_FIELD_TYPES)
    if (not partial or "inference_policy" in fields) and fields.get("inference_policy") is not None:
        _validate_choice("inference_policy", fields.get("inference_policy"), VALID_INFERENCE_POLICIES)
    if "options_json" in fields and fields.get("options_json"):
        parsed = json.loads(fields["options_json"])
        if not isinstance(parsed, list) or not all(isinstance(v, str) for v in parsed):
            raise TrackerValidationError("options_json must be a JSON string array")
    if "linked_task_id" in fields and fields.get("linked_task_id") is not None:
        if not isinstance(fields["linked_task_id"], str) or not fields["linked_task_id"].strip():
            raise TrackerValidationError("linked_task_id must be a task id or null")


def coerce_value_for_field(field, raw_value):
    field_type = field["field_type"]
    if raw_value is None:
        raise TrackerValidationError("value is required")
    if field_type == "boolean":
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str) and raw_value.lower() in ("true", "false"):
            return raw_value.lower() == "true"
        raise TrackerValidationError("boolean value required")
    if field_type in ("number", "scale", "duration"):
        if isinstance(raw_value, bool):
            raise TrackerValidationError("numeric value required")
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise TrackerValidationError("numeric value required")
        if field.get("min_value") is not None and value < field["min_value"]:
            raise TrackerValidationError("value below minimum")
        if field.get("max_value") is not None and value > field["max_value"]:
            raise TrackerValidationError("value above maximum")
        return value
    if field_type == "multi_select":
        if not isinstance(raw_value, list):
            raise TrackerValidationError("multi_select value must be a list")
        values = [str(v) for v in raw_value]
    else:
        values = str(raw_value)
    if field_type in ("select", "multi_select") and field.get("options_json"):
        options = set(json.loads(field["options_json"]))
        check_values = values if isinstance(values, list) else [values]
        if any(v not in options for v in check_values):
            raise TrackerValidationError("value is not an allowed option")
    return values


def init_db():
    migrations = [
        # Version 1: Initial schema
        """
        CREATE TABLE IF NOT EXISTS trackers (
            id                   TEXT PRIMARY KEY,
            user_id              INTEGER NOT NULL,
            name                 TEXT NOT NULL,
            description          TEXT,
            cadence              TEXT NOT NULL DEFAULT 'daily',
            timezone             TEXT,
            start_date           TEXT,
            end_date             TEXT,
            prompt_instructions  TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL,
            archived_at          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trackers_user
            ON trackers(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_trackers_cadence
            ON trackers(cadence);

        CREATE TABLE IF NOT EXISTS tracker_fields (
            id               TEXT PRIMARY KEY,
            tracker_id       TEXT NOT NULL REFERENCES trackers(id),
            user_id          INTEGER NOT NULL,
            name             TEXT NOT NULL,
            field_key        TEXT NOT NULL,
            field_type       TEXT NOT NULL,
            description      TEXT,
            required         INTEGER NOT NULL DEFAULT 0,
            options_json     TEXT,
            unit             TEXT,
            min_value        REAL,
            max_value        REAL,
            inference_policy TEXT NOT NULL DEFAULT 'ask_if_missing',
            sort_order       INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            UNIQUE(tracker_id, field_key)
        );

        CREATE INDEX IF NOT EXISTS idx_tracker_fields_tracker
            ON tracker_fields(tracker_id, sort_order);

        CREATE TABLE IF NOT EXISTS tracker_rows (
            id                TEXT PRIMARY KEY,
            tracker_id        TEXT NOT NULL REFERENCES trackers(id),
            user_id           INTEGER NOT NULL,
            period_start      TEXT NOT NULL,
            period_end        TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'incomplete',
            source            TEXT NOT NULL DEFAULT 'manual',
            source_session_id TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            UNIQUE(tracker_id, period_start)
        );

        CREATE INDEX IF NOT EXISTS idx_tracker_rows_tracker_period
            ON tracker_rows(tracker_id, period_start);
        CREATE INDEX IF NOT EXISTS idx_tracker_rows_status
            ON tracker_rows(status);

        CREATE TABLE IF NOT EXISTS tracker_values (
            id                TEXT PRIMARY KEY,
            row_id            TEXT NOT NULL REFERENCES tracker_rows(id),
            field_id          TEXT NOT NULL REFERENCES tracker_fields(id),
            user_id           INTEGER NOT NULL,
            value_json        TEXT,
            confidence        TEXT,
            source            TEXT NOT NULL DEFAULT 'manual',
            source_session_id TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            UNIQUE(row_id, field_id)
        );

        CREATE INDEX IF NOT EXISTS idx_tracker_values_row
            ON tracker_values(row_id);
        CREATE INDEX IF NOT EXISTS idx_tracker_values_field
            ON tracker_values(field_id);

        CREATE TABLE IF NOT EXISTS tracker_questions (
            id          TEXT PRIMARY KEY,
            tracker_id  TEXT NOT NULL REFERENCES trackers(id),
            row_id      TEXT REFERENCES tracker_rows(id),
            field_id    TEXT REFERENCES tracker_fields(id),
            user_id     INTEGER NOT NULL,
            question    TEXT NOT NULL,
            reason      TEXT,
            status      TEXT NOT NULL DEFAULT 'open',
            answered_at TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tracker_questions_user_status
            ON tracker_questions(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_tracker_questions_tracker
            ON tracker_questions(tracker_id);
        """,
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)
    with db.get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tracker_fields)").fetchall()}
        for col, definition in [
            ("ai_explanation", "TEXT"),
            ("linked_task_id", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE tracker_fields ADD COLUMN {col} {definition}")

        row_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tracker_rows)").fetchall()}
        if "source_context_hash" not in row_cols:
            conn.execute("ALTER TABLE tracker_rows ADD COLUMN source_context_hash TEXT")


# ---------------------------------------------------------------------------
# Tracker CRUD
# ---------------------------------------------------------------------------

def create_tracker(user_id, name, description=None, cadence="daily",
                   timezone_str=None, start_date=None):
    validate_tracker_fields({"cadence": cadence, "timezone": timezone_str})
    tracker_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO trackers (id, user_id, name, description, cadence, timezone, "
            "start_date, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (tracker_id, user_id, name, description, cadence, timezone_str,
             start_date, now, now),
        )
    return tracker_id


def get_tracker(user_id, tracker_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM trackers WHERE id=? AND user_id=?",
            (tracker_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def update_tracker(user_id, tracker_id, **fields):
    allowed = {k: v for k, v in fields.items()
               if k in ("name", "description", "cadence", "timezone", "start_date",
                        "end_date", "prompt_instructions")}
    if not allowed:
        return
    validate_tracker_fields(allowed, partial=True)
    now = datetime.now(timezone.utc).isoformat()
    allowed["updated_at"] = now
    sets = ", ".join(f"{k}=?" for k in allowed)
    vals = list(allowed.values()) + [tracker_id, user_id]
    with db.get_db() as conn:
        conn.execute(f"UPDATE trackers SET {sets} WHERE id=? AND user_id=?", vals)


def archive_tracker(user_id, tracker_id):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE trackers SET archived_at=?, updated_at=? WHERE id=? AND user_id=?",
            (now, now, tracker_id, user_id),
        )


def list_trackers(user_id, include_archived=False):
    with db.get_db() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM trackers WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trackers WHERE user_id=? AND archived_at IS NULL "
                "ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Field CRUD
# ---------------------------------------------------------------------------

def add_field(user_id, tracker_id, name, field_key, field_type,
              description=None, required=False, options_json=None,
              unit=None, min_value=None, max_value=None,
              inference_policy="ask_if_missing", sort_order=0,
              ai_explanation=None, linked_task_id=None):
    validate_field_fields({
        "field_type": field_type,
        "inference_policy": inference_policy,
        "options_json": options_json,
        "linked_task_id": linked_task_id,
    })
    field_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO tracker_fields (id, tracker_id, user_id, name, field_key, "
            "field_type, description, required, options_json, unit, min_value, max_value, "
            "inference_policy, sort_order, ai_explanation, linked_task_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (field_id, tracker_id, user_id, name, field_key, field_type, description,
             1 if required else 0, options_json, unit, min_value, max_value,
             inference_policy, sort_order, ai_explanation, linked_task_id, now, now),
        )
    return field_id


def update_field(user_id, field_id, **fields):
    allowed = {k: v for k, v in fields.items()
               if k in ("name", "description", "required", "options_json", "unit",
                        "min_value", "max_value", "inference_policy", "sort_order",
                        "ai_explanation", "linked_task_id")}
    if not allowed:
        return
    validate_field_fields(allowed, partial=True)
    now = datetime.now(timezone.utc).isoformat()
    allowed["updated_at"] = now
    sets = ", ".join(f"{k}=?" for k in allowed)
    vals = list(allowed.values()) + [field_id, user_id]
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE tracker_fields SET {sets} WHERE id=? AND user_id=?", vals
        )


def delete_field(user_id, field_id):
    with db.get_db() as conn:
        # Remove any values associated with this field first
        conn.execute("DELETE FROM tracker_values WHERE field_id=? AND user_id=?",
                     (field_id, user_id))
        conn.execute("DELETE FROM tracker_fields WHERE id=? AND user_id=?",
                     (field_id, user_id))


def get_field(user_id, field_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tracker_fields WHERE id=? AND user_id=?",
            (field_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def list_fields(tracker_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tracker_fields WHERE tracker_id=? ORDER BY sort_order ASC, created_at ASC",
            (tracker_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Row CRUD
# ---------------------------------------------------------------------------

def get_or_create_row(user_id, tracker_id, period_start, period_end):
    """Get existing row or create a new one for the given period. Returns row dict."""
    now = datetime.now(timezone.utc).isoformat()
    row_id = uuid.uuid4().hex[:8]
    with db.get_db() as conn:
        tracker = conn.execute(
            "SELECT id FROM trackers WHERE id=? AND user_id=?",
            (tracker_id, user_id),
        ).fetchone()
        if not tracker:
            raise TrackerValidationError("tracker not found")
        conn.execute(
            "INSERT OR IGNORE INTO tracker_rows "
            "(id, tracker_id, user_id, period_start, period_end, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (row_id, tracker_id, user_id, period_start, period_end, now, now),
        )
        row = conn.execute(
            "SELECT * FROM tracker_rows WHERE tracker_id=? AND period_start=?",
            (tracker_id, period_start),
        ).fetchone()
    return dict(row)


def mark_row_processed(user_id, row_id, source="agent", source_session_id=None,
                       source_context_hash=None):
    _validate_choice("source", source, VALID_VALUE_SOURCES)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tracker_rows SET source=?, source_session_id=?, source_context_hash=?, "
            "updated_at=? WHERE id=? AND user_id=?",
            (source, source_session_id, source_context_hash, now, row_id, user_id),
        )


def get_row(user_id, row_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tracker_rows WHERE id=? AND user_id=?",
            (row_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def update_row_status(user_id, row_id, status):
    _validate_choice("status", status, VALID_ROW_STATUSES)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tracker_rows SET status=?, updated_at=? WHERE id=? AND user_id=?",
            (status, now, row_id, user_id),
        )


def list_rows(user_id, tracker_id, limit=90):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tracker_rows WHERE tracker_id=? AND user_id=? "
            "ORDER BY period_start DESC LIMIT ?",
            (tracker_id, user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Value CRUD
# ---------------------------------------------------------------------------

def set_value(user_id, row_id, field_id, value_json, source="manual",
              confidence="user_confirmed", source_session_id=None):
    """Upsert a tracker value. value_json should be a JSON-encoded string."""
    _validate_choice("source", source, VALID_VALUE_SOURCES)
    _validate_choice("confidence", confidence, VALID_CONFIDENCES)
    val_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tracker_rows WHERE id=? AND user_id=?",
            (row_id, user_id),
        ).fetchone()
        field = conn.execute(
            "SELECT * FROM tracker_fields WHERE id=? AND user_id=?",
            (field_id, user_id),
        ).fetchone()
        if not row or not field or row["tracker_id"] != field["tracker_id"]:
            raise TrackerValidationError("field does not belong to tracker row")
        json.loads(value_json)
        conn.execute(
            "INSERT INTO tracker_values (id, row_id, field_id, user_id, value_json, "
            "confidence, source, source_session_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(row_id, field_id) DO UPDATE SET "
            "value_json=excluded.value_json, confidence=excluded.confidence, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (val_id, row_id, field_id, user_id, value_json, confidence,
             source, source_session_id, now, now),
        )
    _recalculate_row_status(user_id, row_id)


def _recalculate_row_status(user_id, row_id):
    """Update tracker_rows.status based on current values."""
    with db.get_db() as conn:
        # Get values for this row
        values = conn.execute(
            "SELECT v.field_id, v.source FROM tracker_values v WHERE v.row_id=?",
            (row_id,),
        ).fetchall()
        # Get required fields for this tracker row
        row = conn.execute("SELECT tracker_id FROM tracker_rows WHERE id=?", (row_id,)).fetchone()
        if not row:
            return
        required_fields = conn.execute(
            "SELECT id FROM tracker_fields WHERE tracker_id=? AND required=1",
            (row["tracker_id"],),
        ).fetchall()

    value_map = {v["field_id"]: v["source"] for v in values}
    required_ids = {r["id"] for r in required_fields}
    has_manual = any(v["source"] == "manual" for v in values)

    if has_manual:
        new_status = "manually_edited"
    elif required_ids and not required_ids.issubset(value_map.keys()):
        new_status = "incomplete"
    elif values:
        # Check if any are inferred
        all_inferred = all(v["source"] in ("inferred", "system", "chat", "mcp", "agent")
                           for v in values)
        new_status = "inferred" if all_inferred else "confirmed"
    else:
        new_status = "empty"

    update_row_status(user_id, row_id, new_status)


def get_values_for_row(row_id):
    """Return {field_id: value_dict} for a single row."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tracker_values WHERE row_id=?", (row_id,)
        ).fetchall()
    return {r["field_id"]: dict(r) for r in rows}


def get_values_for_rows(row_ids):
    """Return {row_id: {field_id: value_dict}} for multiple rows."""
    if not row_ids:
        return {}
    placeholders = ",".join("?" * len(row_ids))
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM tracker_values WHERE row_id IN ({placeholders})",
            list(row_ids),
        ).fetchall()
    result = {}
    for r in rows:
        rd = dict(r)
        result.setdefault(rd["row_id"], {})[rd["field_id"]] = rd
    return result


def list_due_periods(user_id, as_of_date):
    """Return tracker periods that ended before as_of_date and do not have a row yet."""
    due = []
    today = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    for tracker in list_trackers(user_id, include_archived=False):
        cadence = tracker["cadence"]
        if cadence == "ad_hoc":
            continue
        if cadence == "daily":
            target = today - timedelta(days=1)
        elif cadence == "weekly":
            if today.weekday() != 0:
                continue
            target = today - timedelta(days=1)
        elif cadence == "monthly":
            if today.day != 1:
                continue
            target = today - timedelta(days=1)
        else:
            continue
        period_start, period_end = derive_period_bounds(target.strftime("%Y-%m-%d"), cadence)
        with db.get_db() as conn:
            exists = conn.execute(
                "SELECT id FROM tracker_rows WHERE tracker_id=? AND user_id=? AND period_start=?",
                (tracker["id"], user_id, period_start),
            ).fetchone()
        if not exists:
            due.append((tracker, period_start, period_end))
    return due


# ---------------------------------------------------------------------------
# Questions CRUD
# ---------------------------------------------------------------------------

def create_question(user_id, tracker_id, question, row_id=None, field_id=None, reason=None):
    q_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        tracker = conn.execute(
            "SELECT id FROM trackers WHERE id=? AND user_id=?",
            (tracker_id, user_id),
        ).fetchone()
        if not tracker:
            raise TrackerValidationError("tracker not found")
        if row_id:
            row = conn.execute(
                "SELECT id, tracker_id FROM tracker_rows WHERE id=? AND user_id=?",
                (row_id, user_id),
            ).fetchone()
            if not row or row["tracker_id"] != tracker_id:
                raise TrackerValidationError("row does not belong to tracker")
        if field_id:
            field = conn.execute(
                "SELECT id, tracker_id FROM tracker_fields WHERE id=? AND user_id=?",
                (field_id, user_id),
            ).fetchone()
            if not field or field["tracker_id"] != tracker_id:
                raise TrackerValidationError("field does not belong to tracker")
        existing = conn.execute(
            "SELECT id FROM tracker_questions WHERE user_id=? AND tracker_id=? "
            "AND COALESCE(row_id,'')=COALESCE(?,'') AND COALESCE(field_id,'')=COALESCE(?,'') "
            "AND status='open'",
            (user_id, tracker_id, row_id, field_id),
        ).fetchone()
        if existing:
            return existing["id"]
        conn.execute(
            "INSERT INTO tracker_questions (id, tracker_id, row_id, field_id, user_id, "
            "question, reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (q_id, tracker_id, row_id, field_id, user_id, question, reason, now, now),
        )
    return q_id


def get_question(user_id, question_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tracker_questions WHERE id=? AND user_id=?",
            (question_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def answer_question(user_id, question_id):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tracker_questions SET status='answered', answered_at=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, now, question_id, user_id),
        )


def dismiss_question(user_id, question_id):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tracker_questions SET status='dismissed', updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, question_id, user_id),
        )


def list_open_questions(user_id, tracker_id=None):
    if tracker_id:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM tracker_questions WHERE user_id=? AND tracker_id=? AND status='open' "
                "ORDER BY created_at ASC",
                (user_id, tracker_id),
            ).fetchall()
    else:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM tracker_questions WHERE user_id=? AND status='open' "
                "ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tagging (uses shared object_tags table)
# ---------------------------------------------------------------------------

def tag_tracker(user_id, tracker_id, tag_ids, tag_source="user_explicit"):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        for tid in tag_ids:
            conn.execute(
                "INSERT OR IGNORE INTO object_tags "
                "(object_kind, object_id, tag_id, tag_source, user_id, created_at) "
                "VALUES ('tracker', ?, ?, ?, ?, ?)",
                (tracker_id, tid, tag_source, user_id, now),
            )


def untag_tracker(tracker_id, tag_id):
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM object_tags WHERE object_kind='tracker' AND object_id=? AND tag_id=?",
            (tracker_id, tag_id),
        )


def get_tags_for_tracker(tracker_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.color, t.kind FROM object_tags ot "
            "JOIN tags t ON ot.tag_id = t.id "
            "WHERE ot.object_kind='tracker' AND ot.object_id=? ORDER BY t.name",
            (tracker_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CSV export helper
# ---------------------------------------------------------------------------

def build_csv(user_id, tracker_id):
    """Build CSV content string for a tracker. Returns (filename, csv_string)."""
    tracker = get_tracker(user_id, tracker_id)
    if not tracker:
        return None, None
    fields = list_fields(tracker_id)
    rows = list_rows(user_id, tracker_id, limit=10000)
    row_ids = [r["id"] for r in rows]
    values_map = get_values_for_rows(row_ids)

    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    header = ["period_start", "period_end"] + [f["field_key"] for f in fields] + ["status"]
    writer.writerow(header)

    for row in rows:
        vals = values_map.get(row["id"], {})
        row_values = []
        for f in fields:
            v = vals.get(f["id"])
            if v:
                try:
                    parsed = json.loads(v["value_json"])
                    if isinstance(parsed, list):
                        row_values.append(", ".join(str(x) for x in parsed))
                    else:
                        row_values.append(str(parsed))
                except Exception:
                    row_values.append(v["value_json"] or "")
            else:
                row_values.append("")
        writer.writerow([row["period_start"], row["period_end"]] + row_values + [row["status"]])

    filename = f"{tracker['name'].lower().replace(' ', '-')}.csv"
    return filename, output.getvalue()
