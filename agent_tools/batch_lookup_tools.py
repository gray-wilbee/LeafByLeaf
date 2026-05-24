"""Batch lookup tools shared by MCP and GPT Actions."""

from __future__ import annotations

import journal
import tasks as tasks_db
import topics
from agent_tools._base import ToolDef


def _batch_get_entries(params: dict, chat_id: int, user_id: int) -> dict:
    entries = []
    errors = []
    for entry_id in params.get("entry_ids", []):
        entry = journal.get_entry_by_id(user_id, entry_id)
        if entry:
            entries.append(entry)
        else:
            errors.append({"entry_id": entry_id, "error": "not found"})
    return {"entries": entries, "errors": errors, "count": len(entries)}


def _batch_get_tasks(params: dict, chat_id: int, user_id: int) -> dict:
    tasks = []
    errors = []
    for task_id in params.get("task_ids", []):
        task = tasks_db.get_task(user_id, task_id)
        if not task:
            errors.append({"task_id": task_id, "error": "not found"})
            continue
        task["depends_on"] = tasks_db.get_depends_on(task["id"])
        task["children"] = tasks_db.get_children(task["id"])
        task["tags"] = topics.get_tags_for_task(task["id"])
        tasks.append(task)
    return {"tasks": tasks, "errors": errors, "count": len(tasks)}


def _public_tag(tag: dict) -> dict:
    return {k: v for k, v in tag.items() if k != "embedding"}


def _batch_get_tags(params: dict, chat_id: int, user_id: int) -> dict:
    tags = []
    errors = []
    for tag_id in params.get("tag_ids", []):
        tag = topics.get_topic(user_id, tag_id) or topics.get_entity(user_id, tag_id)
        if not tag:
            errors.append({"tag_id": tag_id, "error": "not found"})
            continue
        tag = _public_tag(tag)
        tag["children"] = topics.get_children(tag_id)
        tag["parent"] = topics.get_parent(tag_id)
        tag["links"] = topics.get_tag_links(tag_id)
        tags.append(tag)
    return {"tags": tags, "errors": errors, "count": len(tags)}


def _batch_get_tag_notes(params: dict, chat_id: int, user_id: int) -> dict:
    include_archived = params.get("include_archived", False)
    results = []
    for tag_id in params.get("tag_ids", []):
        notes = topics.list_topic_entries(tag_id, include_archived=include_archived)
        results.append({"tag_id": tag_id, "notes": notes, "count": len(notes)})
    return {"results": results, "count": len(results)}


def _batch_get_settings(params: dict, chat_id: int, user_id: int) -> dict:
    settings = []
    for key in params.get("keys", []):
        settings.append({"key": key, "value": topics.get_setting(user_id, key)})
    return {"settings": settings, "count": len(settings)}


TOOLS = [
    ToolDef(
        name="batch_get_entries",
        description="Get full content for multiple journal entries by ID in one call. Prefer this after search_entries returns several IDs.",
        input_schema={
            "type": "object",
            "properties": {
                "entry_ids": {"type": "array", "items": {"type": "string"}, "description": "Journal entry IDs to fetch"},
            },
            "required": ["entry_ids"],
        },
        handler=_batch_get_entries,
    ),
    ToolDef(
        name="batch_get_tasks",
        description="Get full details for multiple tasks by ID in one call, including dependencies, children, and tags.",
        input_schema={
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}, "description": "Task IDs to fetch"},
            },
            "required": ["task_ids"],
        },
        handler=_batch_get_tasks,
    ),
    ToolDef(
        name="batch_get_tags",
        description="Get full details for multiple topics or entities by ID in one call, including hierarchy and links.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Topic or entity IDs to fetch"},
            },
            "required": ["tag_ids"],
        },
        handler=_batch_get_tags,
    ),
    ToolDef(
        name="batch_get_tag_notes",
        description="Get notes for multiple topics or entities in one call.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Topic or entity IDs"},
                "include_archived": {"type": "boolean", "default": False},
            },
            "required": ["tag_ids"],
        },
        handler=_batch_get_tag_notes,
    ),
    ToolDef(
        name="batch_get_settings",
        description="Get multiple VoiceJournal settings by key in one call.",
        input_schema={
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Setting keys to fetch"},
            },
            "required": ["keys"],
        },
        handler=_batch_get_settings,
    ),
]
