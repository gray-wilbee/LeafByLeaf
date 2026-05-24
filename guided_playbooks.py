from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import db


DEFAULT_GUIDED_PLAYBOOKS = {
    "clear_my_head": {
        "id": "builtin:clear_my_head",
        "title": "Clear My Head",
        "description": "Untangle scattered thoughts and find the next small point of clarity.",
        "target_question_count": 5,
        "steps": [
            {"name": "Surface the noise", "purpose": "Name what is currently taking up mental space.", "examples": ["What has been taking up the most space in your mind today?"]},
            {"name": "Separate facts from feelings", "purpose": "Distinguish what happened from what it brought up.", "examples": ["What happened, and what feelings are attached to it?"]},
            {"name": "Find the weight", "purpose": "Identify the most emotionally loaded part.", "examples": ["What part of this feels heavier than the rest?"]},
            {"name": "Name the need", "purpose": "Find the need underneath the noise.", "examples": ["What do you need right now that you have not named yet?"]},
            {"name": "Make it lighter", "purpose": "Move toward one manageable next step.", "examples": ["What would make this feel a little lighter?"]},
        ],
    },
    "process_something_hard": {
        "id": "builtin:process_something_hard",
        "title": "Process Something Hard",
        "description": "Reflect on something difficult with honesty and care.",
        "target_question_count": 5,
        "steps": [
            {"name": "What happened", "purpose": "Let the user tell the story plainly.", "examples": ["What happened?"]},
            {"name": "What it brought up", "purpose": "Explore the emotional response.", "examples": ["What did it bring up in you?"]},
            {"name": "Unresolved part", "purpose": "Name what still feels unfinished.", "examples": ["What part still feels unresolved?"]},
            {"name": "Compassion", "purpose": "Invite a kinder perspective.", "examples": ["What would compassion say here?"]},
            {"name": "Carry forward", "purpose": "Find what to keep from the experience.", "examples": ["What do you want to carry forward from this?"]},
        ],
    },
    "plan_my_day": {
        "id": "builtin:plan_my_day",
        "title": "Plan My Day",
        "description": "Turn the day into a small, practical plan.",
        "target_question_count": 5,
        "steps": [
            {"name": "Main priority", "purpose": "Identify what matters most.", "examples": ["What matters most today?"]},
            {"name": "Must do", "purpose": "Separate commitments from wishes.", "examples": ["What must get done today?"]},
            {"name": "Risks", "purpose": "Anticipate friction.", "examples": ["What could derail you?"]},
            {"name": "Next action", "purpose": "Create an immediate start point.", "examples": ["What is the smallest next action?"]},
            {"name": "Close the day", "purpose": "Define a sane finish line.", "examples": ["How do you want to end the day?"]},
        ],
    },
    "end_of_day_review": {
        "id": "builtin:end_of_day_review",
        "title": "End-of-Day Review",
        "description": "Reflect, learn, and close the day.",
        "target_question_count": 5,
        "steps": [
            {"name": "What happened", "purpose": "Recall the day.", "examples": ["What happened today?"]},
            {"name": "What went well", "purpose": "Notice wins.", "examples": ["What went well?"]},
            {"name": "What felt off", "purpose": "Name friction without spiraling.", "examples": ["What felt off?"]},
            {"name": "What I learned", "purpose": "Find insight.", "examples": ["What did you learn?"]},
            {"name": "Release", "purpose": "Close the loop.", "examples": ["What can you release tonight?"]},
        ],
    },
    "decision_journal": {
        "id": "builtin:decision_journal",
        "title": "Decision Journal",
        "description": "Think through a choice clearly.",
        "target_question_count": 5,
        "steps": [
            {"name": "Decision", "purpose": "Name the choice.", "examples": ["What decision are you facing?"]},
            {"name": "Options", "purpose": "Lay out real alternatives.", "examples": ["What are the real options?"]},
            {"name": "Optimize for", "purpose": "Clarify criteria.", "examples": ["What are you optimizing for?"]},
            {"name": "Fear", "purpose": "Surface hidden pressure.", "examples": ["What are you afraid of?"]},
            {"name": "Wisest choice", "purpose": "Move toward judgment.", "examples": ["What choice seems wisest right now?"]},
        ],
    },
}


def init_db() -> None:
    migrations = [
        """
        CREATE TABLE IF NOT EXISTS guided_playbooks (
            id                    TEXT PRIMARY KEY,
            user_id               INTEGER NOT NULL,
            title                 TEXT NOT NULL,
            description           TEXT,
            target_question_count INTEGER NOT NULL DEFAULT 5,
            steps_json            TEXT NOT NULL,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            deleted_at            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_guided_playbooks_user ON guided_playbooks(user_id, deleted_at);
        """,
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _builtin_rows() -> list[dict]:
    return [{**pb, "is_builtin": True} for pb in DEFAULT_GUIDED_PLAYBOOKS.values()]


def _clean_steps(steps: list | None) -> list[dict]:
    cleaned = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "").strip()
        purpose = str(step.get("purpose") or "").strip()
        examples = [
            str(e).strip()
            for e in (step.get("examples") or [])
            if str(e).strip()
        ][:5]
        if name or purpose or examples:
            cleaned.append({"name": name or "Step", "purpose": purpose, "examples": examples})
    return cleaned[:12]


def _serialize_row(row) -> dict:
    item = dict(row)
    try:
        item["steps"] = json.loads(item.pop("steps_json") or "[]")
    except Exception:
        item["steps"] = []
    item["is_builtin"] = False
    return item


def list_playbooks(user_id: int) -> list[dict]:
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, description, target_question_count, steps_json, created_at, updated_at
            FROM guided_playbooks
            WHERE user_id=? AND deleted_at IS NULL
            ORDER BY title COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
    return _builtin_rows() + [_serialize_row(r) for r in rows]


def get_playbook(user_id: int, playbook_id: str | None) -> dict | None:
    if not playbook_id:
        return None
    if playbook_id.startswith("builtin:"):
        key = playbook_id.split(":", 1)[1]
        pb = DEFAULT_GUIDED_PLAYBOOKS.get(key)
        return {**pb, "is_builtin": True} if pb else None
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT id, title, description, target_question_count, steps_json, created_at, updated_at
            FROM guided_playbooks
            WHERE user_id=? AND id=? AND deleted_at IS NULL
            """,
            (user_id, playbook_id),
        ).fetchone()
    return _serialize_row(row) if row else None


def save_playbook(user_id: int, data: dict, playbook_id: str | None = None) -> dict:
    title = str(data.get("title") or "").strip()[:120]
    if not title:
        raise ValueError("title required")
    description = str(data.get("description") or "").strip()[:500]
    steps = _clean_steps(data.get("steps"))
    if not steps:
        raise ValueError("at least one step required")
    target = int(data.get("target_question_count") or len(steps))
    target = max(1, min(target, 12))
    now = _now()
    if playbook_id and playbook_id.startswith("builtin:"):
        raise ValueError("built-in playbooks cannot be edited")
    if playbook_id:
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT id FROM guided_playbooks WHERE user_id=? AND id=? AND deleted_at IS NULL",
                (user_id, playbook_id),
            ).fetchone()
            if not row:
                raise KeyError("not found")
            conn.execute(
                """
                UPDATE guided_playbooks
                SET title=?, description=?, target_question_count=?, steps_json=?, updated_at=?
                WHERE user_id=? AND id=?
                """,
                (title, description, target, json.dumps(steps), now, user_id, playbook_id),
            )
        return get_playbook(user_id, playbook_id)

    new_id = uuid.uuid4().hex[:12]
    with db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO guided_playbooks
            (id, user_id, title, description, target_question_count, steps_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id, user_id, title, description, target, json.dumps(steps), now, now),
        )
    return get_playbook(user_id, new_id)


def duplicate_playbook(user_id: int, playbook_id: str) -> dict:
    original = get_playbook(user_id, playbook_id)
    if not original:
        raise KeyError("not found")
    return save_playbook(user_id, {
        "title": f"{original['title']} Copy",
        "description": original.get("description") or "",
        "target_question_count": original.get("target_question_count") or len(original.get("steps") or []),
        "steps": original.get("steps") or [],
    })


def delete_playbook(user_id: int, playbook_id: str) -> None:
    if playbook_id.startswith("builtin:"):
        raise ValueError("built-in playbooks cannot be deleted")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE guided_playbooks SET deleted_at=?, updated_at=? WHERE user_id=? AND id=?",
            (_now(), _now(), user_id, playbook_id),
        )
