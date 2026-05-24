from __future__ import annotations

import uuid
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
import db
import topics

VALID_ITEM_TYPES = {"decision", "open_question", "revisit_later"}
VALID_STATUSES = {"open", "decided", "deferred", "reversed"}
VALID_REVIEW_STATUSES = {"accepted", "suggested", "dismissed"}
VALID_SOURCES = {"manual", "agent", "extraction"}
VALID_CONFIDENCES = {None, "low", "medium", "high"}


class DecisionValidationError(ValueError):
    pass


class DuplicateDecisionError(ValueError):
    pass


def _validate_choice(name, value, valid):
    if value not in valid:
        raise DecisionValidationError(f"invalid {name}: {value}")


def normalize_title(title: str) -> str:
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [
        w for w in text.split()
        if w not in {"a", "an", "the", "to", "that", "this", "we", "i"}
    ]
    return " ".join(words)


def is_fuzzy_duplicate_title(a: str, b: str, threshold: float = 0.86) -> bool:
    na = normalize_title(a)
    nb = normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def find_duplicate_item(user_id, item_type, title, exclude_id=None):
    _validate_choice("item_type", item_type, VALID_ITEM_TYPES)
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, item_type FROM decision_log_items "
            "WHERE user_id=? AND soft_deleted=0 AND review_status IN ('accepted','suggested') "
            "AND item_type=?",
            (user_id, item_type),
        ).fetchall()
    for row in rows:
        if exclude_id and row["id"] == exclude_id:
            continue
        if is_fuzzy_duplicate_title(title, row["title"]):
            return dict(row)
    return None


def validate_item_fields(fields: dict, partial=False):
    if not partial or "item_type" in fields:
        _validate_choice("item_type", fields.get("item_type"), VALID_ITEM_TYPES)
    if not partial or "status" in fields:
        _validate_choice("status", fields.get("status", "open"), VALID_STATUSES)
    if not partial or "review_status" in fields:
        _validate_choice("review_status", fields.get("review_status", "accepted"), VALID_REVIEW_STATUSES)
    if not partial or "source" in fields:
        _validate_choice("source", fields.get("source", "manual"), VALID_SOURCES)
    if "confidence" in fields:
        _validate_choice("confidence", fields.get("confidence"), VALID_CONFIDENCES)


def init_db():
    migrations = [
        # Version 1: Initial schema
        """
        CREATE TABLE IF NOT EXISTS decision_log_items (
            id                TEXT PRIMARY KEY,
            user_id           INTEGER NOT NULL,
            item_type         TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'open',
            review_status     TEXT NOT NULL DEFAULT 'accepted',
            title             TEXT NOT NULL,
            content           TEXT,
            rationale         TEXT,
            alternatives      TEXT,
            review_at         TEXT,
            confidence        TEXT,
            source            TEXT NOT NULL DEFAULT 'manual',
            source_session_id TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            decided_at        TEXT,
            soft_deleted      INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_decision_log_user
            ON decision_log_items(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_decision_log_type
            ON decision_log_items(item_type);
        CREATE INDEX IF NOT EXISTS idx_decision_log_status
            ON decision_log_items(status);
        CREATE INDEX IF NOT EXISTS idx_decision_log_review_status
            ON decision_log_items(review_status);
        CREATE INDEX IF NOT EXISTS idx_decision_log_source
            ON decision_log_items(source_session_id);
        """,
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)


# ---------------------------------------------------------------------------
# Decision CRUD
# ---------------------------------------------------------------------------

def create_item(user_id, item_type, title, content=None, rationale=None,
                alternatives=None, status="open", review_status="accepted",
                source="manual", source_session_id=None, confidence=None,
                review_at=None, reject_duplicates=False):
    """Create a decision log item and return its 8-char hex ID."""
    validate_item_fields({
        "item_type": item_type,
        "status": status,
        "review_status": review_status,
        "source": source,
        "confidence": confidence,
    })
    if reject_duplicates and find_duplicate_item(user_id, item_type, title):
        raise DuplicateDecisionError("duplicate decision log item")
    item_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO decision_log_items "
            "(id, user_id, item_type, status, review_status, title, content, rationale, "
            "alternatives, review_at, confidence, source, source_session_id, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, user_id, item_type, status, review_status, title, content,
             rationale, alternatives, review_at, confidence, source,
             source_session_id, now, now),
        )
    return item_id


def get_item(user_id, item_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM decision_log_items WHERE id=? AND user_id=? AND soft_deleted=0",
            (item_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def update_item(user_id, item_id, **fields):
    allowed = {k: v for k, v in fields.items()
               if k in ("item_type", "status", "review_status", "title", "content",
                        "rationale", "alternatives", "review_at", "confidence",
                        "decided_at")}
    if not allowed:
        return
    validate_item_fields(allowed, partial=True)
    item_type = allowed.get("item_type")
    title = allowed.get("title")
    if title:
        current = get_item(user_id, item_id)
        if current:
            item_type = item_type or current["item_type"]
            if find_duplicate_item(user_id, item_type, title, exclude_id=item_id):
                raise DuplicateDecisionError("duplicate decision log item")
    now = datetime.now(timezone.utc).isoformat()
    allowed["updated_at"] = now
    if "status" in allowed and allowed["status"] == "decided":
        allowed.setdefault("decided_at", now)
    sets = ", ".join(f"{k}=?" for k in allowed)
    vals = list(allowed.values()) + [item_id, user_id]
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE decision_log_items SET {sets} WHERE id=? AND user_id=?", vals
        )


def soft_delete_item(user_id, item_id):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE decision_log_items SET soft_deleted=1, updated_at=? WHERE id=? AND user_id=?",
            (now, item_id, user_id),
        )


def list_items(user_id, item_type=None, status=None, review_status=None):
    clauses = ["user_id=?", "soft_deleted=0"]
    params = [user_id]
    if item_type:
        clauses.append("item_type=?")
        params.append(item_type)
    if status:
        clauses.append("status=?")
        params.append(status)
    if review_status:
        clauses.append("review_status=?")
        params.append(review_status)
    where = " AND ".join(clauses)
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM decision_log_items WHERE {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def list_for_tags(user_id, tag_ids, include_children=True):
    """Return accepted decision log items tagged with any of tag_ids."""
    if not tag_ids:
        return []
    all_ids = set(tag_ids)
    if include_children:
        for tid in list(tag_ids):
            all_ids.update(topics.get_descendants(tid))
    placeholders = ",".join("?" * len(all_ids))
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT d.* FROM decision_log_items d "
            f"JOIN object_tags ot ON ot.object_kind='decision' AND ot.object_id=d.id "
            f"WHERE d.user_id=? AND d.soft_deleted=0 AND d.review_status='accepted' "
            f"AND ot.tag_id IN ({placeholders}) "
            f"ORDER BY d.created_at DESC",
            [user_id] + list(all_ids),
        ).fetchall()
    # Deduplicate (a decision tagged with multiple matching tags appears once)
    seen = set()
    result = []
    for r in rows:
        rd = dict(r)
        if rd["id"] not in seen:
            seen.add(rd["id"])
            result.append(rd)
    return result


def accept_suggestion(user_id, item_id):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE decision_log_items SET review_status='accepted', updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, item_id, user_id),
        )


def dismiss_suggestion(user_id, item_id):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE decision_log_items SET review_status='dismissed', updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, item_id, user_id),
        )


# ---------------------------------------------------------------------------
# Tagging (uses shared object_tags table)
# ---------------------------------------------------------------------------

def tag_decision(user_id, decision_id, tag_ids, tag_source="user_explicit"):
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        for tid in tag_ids:
            conn.execute(
                "INSERT OR IGNORE INTO object_tags "
                "(object_kind, object_id, tag_id, tag_source, user_id, created_at) "
                "VALUES ('decision', ?, ?, ?, ?, ?)",
                (decision_id, tid, tag_source, user_id, now),
            )


def untag_decision(decision_id, tag_id):
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM object_tags WHERE object_kind='decision' AND object_id=? AND tag_id=?",
            (decision_id, tag_id),
        )


def get_tags_for_decision(decision_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.color, t.kind FROM object_tags ot "
            "JOIN tags t ON ot.tag_id = t.id "
            "WHERE ot.object_kind='decision' AND ot.object_id=? ORDER BY t.name",
            (decision_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_decision_tag_map(user_id, decision_ids):
    """Return {decision_id: [tag_dicts]} for the given decision IDs."""
    if not decision_ids:
        return {}
    with db.get_db() as conn:
        placeholders = ",".join("?" * len(decision_ids))
        rows = conn.execute(
            f"SELECT ot.object_id AS decision_id, t.id, t.name, t.kind, t.color "
            f"FROM object_tags ot "
            f"JOIN tags t ON t.id = ot.tag_id "
            f"WHERE ot.object_kind = 'decision' AND ot.object_id IN ({placeholders}) AND t.user_id = ? "
            f"ORDER BY t.kind, t.name",
            list(decision_ids) + [user_id],
        ).fetchall()
    result = {}
    for r in rows:
        rd = dict(r)
        did = rd.pop("decision_id")
        result.setdefault(did, []).append(rd)
    return result
