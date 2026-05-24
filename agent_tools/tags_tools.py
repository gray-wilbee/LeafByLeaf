"""Agent tools for topic and entity (tag) CRUD and search."""

from __future__ import annotations

import topics
from agent_tools._base import ToolDef


def _public_tag(tag: dict) -> dict:
    """Return tag data that is safe and useful for the model context."""
    return {k: v for k, v in tag.items() if k != "embedding"}


def _search_tags(params: dict, chat_id: int, user_id: int) -> dict:
    query = params.get("query", "")
    kind = params.get("kind")
    limit = params.get("limit", 20)
    results = topics.search_tags(user_id, query, kind=kind, limit=limit)
    return {"tags": [_public_tag(t) for t in results], "count": len(results)}


def _list_tags(params: dict, chat_id: int, user_id: int) -> dict:
    kind = params.get("kind")
    if kind == "topic":
        tags = topics.list_topics(user_id)
    elif kind == "entity":
        tags = topics.list_entities(user_id)
    else:
        tags = topics.list_topics(user_id) + topics.list_entities(user_id)
    slim = []
    for t in tags:
        slim.append({
            "id": t["id"],
            "kind": t["kind"],
            "name": t["name"],
            "color": t.get("color"),
            "description": (t.get("description") or "")[:200],
            "entry_count": t.get("entry_count", 0),
            "parent_name": t.get("parent_name"),
        })
    return {"tags": slim, "count": len(slim)}


def _get_tag(params: dict, chat_id: int, user_id: int) -> dict:
    tag_id = params["tag_id"]
    tag = topics.get_topic(user_id, tag_id) or topics.get_entity(user_id, tag_id)
    if not tag:
        return {"error": f"Tag {tag_id} not found"}
    tag = _public_tag(tag)
    tag["children"] = topics.get_children(tag_id)
    parent = topics.get_parent(tag_id)
    tag["parent"] = parent
    tag["links"] = topics.get_tag_links(tag_id)
    return tag


def _create_tag(params: dict, chat_id: int, user_id: int) -> dict:
    kind = params.get("kind", "topic")
    name = params["name"]
    desc = params.get("description")
    color = params.get("color")
    parent_tag_id = params.get("parent_tag_id")
    if kind == "entity":
        tag_id = topics.create_entity(user_id, name, description=desc, color=color, parent_tag_id=parent_tag_id)
    else:
        tag_id = topics.create_topic(user_id, name, description=desc, color=color, parent_tag_id=parent_tag_id)
    link_to_tag_ids = params.get("link_to_tag_ids") or []
    for linked_id in link_to_tag_ids:
        topics.add_tag_link(user_id, tag_id, linked_id, source="agent")
    return {"id": tag_id, "name": name, "kind": kind}


def _batch_create_tags(params: dict, chat_id: int, user_id: int) -> dict:
    created = []
    errors = []
    for item in params.get("tags", []):
        try:
            created.append(_create_tag(item, chat_id, user_id))
        except Exception as exc:
            errors.append({"input": item, "error": str(exc)})
    return {
        "created": created,
        "errors": errors,
        "affected_ids": [t["id"] for t in created if t.get("id")],
    }


def _update_tag(params: dict, chat_id: int, user_id: int) -> dict:
    tag_id = params["tag_id"]
    fields = {k: v for k, v in params.items() if k != "tag_id"}
    tag = topics.get_topic(user_id, tag_id) or topics.get_entity(user_id, tag_id)
    if not tag:
        return {"error": f"Tag {tag_id} not found"}
    if tag["kind"] == "entity":
        topics.update_entity(user_id, tag_id, **fields)
    else:
        topics.update_topic(user_id, tag_id, **fields)
    return {"id": tag_id, "updated": list(fields.keys())}


def _batch_update_tags(params: dict, chat_id: int, user_id: int) -> dict:
    updated = []
    errors = []
    for item in params.get("updates", []):
        try:
            result = _update_tag(item, chat_id, user_id)
            if result.get("error"):
                errors.append({"input": item, "error": result["error"]})
            else:
                updated.append(result)
        except Exception as exc:
            errors.append({"input": item, "error": str(exc)})
    return {
        "updated": updated,
        "errors": errors,
        "affected_ids": [t["id"] for t in updated if t.get("id")],
    }


def _delete_tag(params: dict, chat_id: int, user_id: int) -> dict:
    tag_id = params["tag_id"]
    tag = topics.get_topic(user_id, tag_id) or topics.get_entity(user_id, tag_id)
    if not tag:
        return {"error": f"Tag {tag_id} not found"}
    name = tag["name"]
    if tag["kind"] == "entity":
        topics.delete_entity(user_id, tag_id)
    else:
        topics.delete_topic(user_id, tag_id)
    return {"id": tag_id, "deleted": name}


def _merge_tags(params: dict, chat_id: int, user_id: int) -> dict:
    source_id = params["source_id"]
    target_id = params["target_id"]
    topics.merge_topics(source_id, target_id)
    return {"merged_from": source_id, "merged_into": target_id}


def _batch_merge_tags(params: dict, chat_id: int, user_id: int) -> dict:
    merged = []
    errors = []
    for item in params.get("merges", []):
        try:
            merged.append(_merge_tags(item, chat_id, user_id))
        except Exception as exc:
            errors.append({"input": item, "error": str(exc)})
    return {
        "merged": merged,
        "errors": errors,
        "affected_ids": [m["merged_into"] for m in merged if m.get("merged_into")],
    }


def _get_tag_notes(params: dict, chat_id: int, user_id: int) -> dict:
    tag_id = params["tag_id"]
    include_archived = params.get("include_archived", False)
    entries = topics.list_topic_entries(tag_id, include_archived=include_archived)
    return {"notes": entries, "count": len(entries)}


def _add_tag_note(params: dict, chat_id: int, user_id: int) -> dict:
    tag_id = params["tag_id"]
    content = params["content"]
    from datetime import date
    note_date = params.get("date") or date.today().isoformat()
    entry_id = topics.upsert_topic_entry(user_id, tag_id, note_date, content)
    return {"id": entry_id, "tag_id": tag_id, "date": note_date}


TOOLS = [
    ToolDef(
        name="search_tags",
        description="Search topics and entities by name. Returns matching tags with IDs, names, kinds, and descriptions.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword to match against tag names"},
                "kind": {"type": "string", "enum": ["topic", "entity"], "description": "Filter by kind"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": ["query"],
        },
        handler=_search_tags,
    ),
    ToolDef(
        name="list_tags",
        description="List all topics, entities, or both. Returns name, kind, color, description, and note count for each.",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["topic", "entity"], "description": "Filter to only topics or entities. Omit for both."},
            },
            "required": [],
        },
        handler=_list_tags,
    ),
    ToolDef(
        name="get_tag",
        description="Get full details of a topic or entity by ID, including children, parent, and backlinks.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_id": {"type": "integer", "description": "Tag ID"},
            },
            "required": ["tag_id"],
        },
        handler=_get_tag,
    ),
    ToolDef(
        name="create_tag",
        description="Create a new topic or entity.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["topic", "entity"], "default": "topic"},
                "description": {"type": "string"},
                "color": {"type": "string", "description": "Hex color code, e.g. '#B85C2A'"},
                "parent_tag_id": {"type": "integer", "description": "ID of parent tag to create this as a subtopic"},
                "link_to_tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Tag IDs to back-link to this new tag via tag_links"},
            },
            "required": ["name"],
        },
        handler=_create_tag,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="batch_create_tags",
        description="Create multiple topics or entities in one call. Prefer this over repeated create_tag calls when creating several tags.",
        input_schema={
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "kind": {"type": "string", "enum": ["topic", "entity"], "default": "topic"},
                            "description": {"type": "string"},
                            "color": {"type": "string"},
                            "parent_tag_id": {"type": "integer"},
                            "link_to_tag_ids": {"type": "array", "items": {"type": "integer"}},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["tags"],
        },
        handler=_batch_create_tags,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="update_tag",
        description="Update fields of a topic or entity: name, description, summary, color, keywords, or parent_tag_id.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_id": {"type": "integer"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "summary": {"type": "string"},
                "color": {"type": "string"},
                "keywords": {"type": "string", "description": "Comma-separated keyword aliases"},
                "parent_tag_id": {"type": ["integer", "null"], "description": "ID of parent tag for hierarchy, or null to make it a root tag"},
            },
            "required": ["tag_id"],
        },
        handler=_update_tag,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="batch_update_tags",
        description="Update multiple topics or entities in one call. Prefer this over repeated update_tag calls for hierarchy or metadata cleanup.",
        input_schema={
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tag_id": {"type": "integer"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "summary": {"type": "string"},
                            "color": {"type": "string"},
                            "keywords": {"type": "string"},
                            "parent_tag_id": {"type": ["integer", "null"]},
                        },
                        "required": ["tag_id"],
                    },
                },
            },
            "required": ["updates"],
        },
        handler=_batch_update_tags,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="delete_tag",
        description="Permanently delete a topic or entity and its notes, tags, and scoped chats. Cannot be undone.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_id": {"type": "integer"},
            },
            "required": ["tag_id"],
        },
        handler=_delete_tag,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="merge_tags",
        description="Merge one topic into another: moves all notes, tags, and links from source to target, then deletes source.",
        input_schema={
            "type": "object",
            "properties": {
                "source_id": {"type": "integer", "description": "Tag ID to merge from (will be deleted)"},
                "target_id": {"type": "integer", "description": "Tag ID to merge into (will be kept)"},
            },
            "required": ["source_id", "target_id"],
        },
        handler=_merge_tags,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="batch_merge_tags",
        description="Merge multiple source tags into target tags in one call. Prefer this over repeated merge_tags calls.",
        input_schema={
            "type": "object",
            "properties": {
                "merges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_id": {"type": "integer"},
                            "target_id": {"type": "integer"},
                        },
                        "required": ["source_id", "target_id"],
                    },
                },
            },
            "required": ["merges"],
        },
        handler=_batch_merge_tags,
        mutates=True,
        object_kind="tag",
    ),
    ToolDef(
        name="get_tag_notes",
        description="Get all notes (entries) stored under a topic or entity.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_id": {"type": "integer"},
                "include_archived": {"type": "boolean", "default": False},
            },
            "required": ["tag_id"],
        },
        handler=_get_tag_notes,
    ),
    ToolDef(
        name="add_tag_note",
        description="Add or update a note under a topic or entity for a given date.",
        input_schema={
            "type": "object",
            "properties": {
                "tag_id": {"type": "integer"},
                "content": {"type": "string", "description": "Markdown content of the note"},
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format (defaults to today)"},
            },
            "required": ["tag_id", "content"],
        },
        handler=_add_tag_note,
        mutates=True,
        object_kind="tag",
    ),
]
