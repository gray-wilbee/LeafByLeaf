"""Agent tools for task CRUD, search, and batch operations."""

from __future__ import annotations

import tasks as tasks_db
import topics
from agent_tools._base import ToolDef


def _search_tasks(params: dict, chat_id: int, user_id: int) -> dict:
    query = params.get("query", "")
    status = params.get("status")
    tag_ids = params.get("tag_ids")
    limit = params.get("limit", 20)

    if query:
        # Full search across all statuses via list + filter
        all_tasks = tasks_db.list_tasks_sorted(
            user_id, show_done=True, show_cancelled=True, tag_ids=tag_ids
        )
        q_lower = query.lower()
        results = [
            t for t in all_tasks
            if q_lower in (t.get("title") or "").lower()
            or q_lower in (t.get("description") or "").lower()
        ]
        if status:
            results = [t for t in results if t["status"] == status]
    else:
        # No query — list with filters
        show_done = status in ("done", None)
        show_cancelled = status in ("cancelled", None)
        if status and status not in ("done", "cancelled"):
            results = tasks_db.list_tasks_sorted(
                user_id, show_done=False, show_cancelled=False, tag_ids=tag_ids
            )
            results = [t for t in results if t["status"] == status]
        else:
            results = tasks_db.list_tasks_sorted(
                user_id, show_done=show_done, show_cancelled=show_cancelled, tag_ids=tag_ids
            )
    results = results[:limit]
    slim = []
    for t in results:
        slim.append({
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "priority": t.get("priority"),
            "due_at": t.get("due_at"),
            "emoji": t.get("emoji"),
            "description": (t.get("description") or "")[:200],
        })
    return {"tasks": slim, "count": len(slim)}


def _get_task(params: dict, chat_id: int, user_id: int) -> dict:
    task = tasks_db.get_task(user_id, params["task_id"])
    if not task:
        return {"error": f"Task {params['task_id']} not found"}
    # Include dependencies and tags
    task["depends_on"] = tasks_db.get_depends_on(task["id"])
    task["children"] = tasks_db.get_children(task["id"])
    task["tags"] = topics.get_tags_for_task(task["id"])
    return task


def _create_task(params: dict, chat_id: int, user_id: int) -> dict:
    task_id = tasks_db.create_task(
        user_id,
        title=params["title"],
        description=params.get("description"),
        emoji=params.get("emoji"),
        priority=params.get("priority", "medium"),
        due_at=params.get("due_at"),
        source="agent",
        source_session_id=str(chat_id),
    )
    tag_ids = params.get("tag_ids") or []
    if tag_ids:
        topics.tag_task(user_id, task_id, tag_ids, tag_source="agent")
    return {"id": task_id, "title": params["title"]}


def _update_task(params: dict, chat_id: int, user_id: int) -> dict:
    task_id = params["task_id"]
    fields = {k: v for k, v in params.items() if k != "task_id"}
    task = tasks_db.get_task(user_id, task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    tasks_db.update_task(user_id, task_id, **fields)
    return {"id": task_id, "updated": list(fields.keys())}


def _delete_task(params: dict, chat_id: int, user_id: int) -> dict:
    task_id = params["task_id"]
    task = tasks_db.get_task(user_id, task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    title = task["title"]
    tasks_db.delete_task(user_id, task_id)
    return {"id": task_id, "deleted": title}


def _batch_update_tasks(params: dict, chat_id: int, user_id: int) -> dict:
    task_ids = params["task_ids"]
    updates = params["updates"]
    updated = []
    for tid in task_ids:
        task = tasks_db.get_task(user_id, tid)
        if task:
            tasks_db.update_task(user_id, tid, **updates)
            updated.append(tid)
    return {"affected_ids": updated, "count": len(updated), "updates": updates}


def _batch_delete_tasks(params: dict, chat_id: int, user_id: int) -> dict:
    task_ids = params["task_ids"]
    deleted = []
    for tid in task_ids:
        task = tasks_db.get_task(user_id, tid)
        if task:
            tasks_db.delete_task(user_id, tid)
            deleted.append(tid)
    return {"affected_ids": deleted, "count": len(deleted)}


TOOLS = [
    ToolDef(
        name="search_tasks",
        description="Search tasks by title keyword, optionally filtered by status or tag IDs. Returns a list of matching tasks with their IDs, titles, statuses, and priorities.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword to match against task titles"},
                "status": {"type": "string", "enum": ["open", "waiting", "done", "cancelled"], "description": "Filter by status"},
                "tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Filter to tasks tagged with any of these tag IDs"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": [],
        },
        handler=_search_tasks,
    ),
    ToolDef(
        name="get_task",
        description="Get full details of a single task by ID, including description, dependencies, children, and tags.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "8-char hex task ID"},
            },
            "required": ["task_id"],
        },
        handler=_get_task,
    ),
    ToolDef(
        name="create_task",
        description="Create a new task. Returns the new task's ID.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "emoji": {"type": "string", "description": "Single emoji for the task"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"], "default": "medium"},
                "due_at": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                "tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Tag IDs (topics or entities) to link to this task at creation"},
            },
            "required": ["title"],
        },
        handler=_create_task,
        mutates=True,
        object_kind="task",
    ),
    ToolDef(
        name="update_task",
        description="Update one or more fields of an existing task.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "8-char hex task ID"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "waiting", "done", "cancelled"]},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "due_at": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                "emoji": {"type": "string"},
                "waiting_reason": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=_update_task,
        mutates=True,
        object_kind="task",
    ),
    ToolDef(
        name="delete_task",
        description="Permanently delete a task and its links. This cannot be undone.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "8-char hex task ID"},
            },
            "required": ["task_id"],
        },
        handler=_delete_task,
        mutates=True,
        object_kind="task",
    ),
    ToolDef(
        name="batch_update_tasks",
        description="Update the same fields on multiple tasks at once. Use after searching to apply bulk changes.",
        input_schema={
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}, "description": "List of task IDs to update"},
                "updates": {
                    "type": "object",
                    "description": "Fields to update on each task",
                    "properties": {
                        "status": {"type": "string", "enum": ["open", "waiting", "done", "cancelled"]},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                        "due_at": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "required": ["task_ids", "updates"],
        },
        handler=_batch_update_tasks,
        mutates=True,
        object_kind="task",
    ),
    ToolDef(
        name="batch_delete_tasks",
        description="Permanently delete multiple tasks at once. This cannot be undone.",
        input_schema={
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}, "description": "List of task IDs to delete"},
            },
            "required": ["task_ids"],
        },
        handler=_batch_delete_tasks,
        mutates=True,
        object_kind="task",
    ),
]
