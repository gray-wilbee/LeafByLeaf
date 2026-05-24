"""Agent tools for object tagging and tag links."""

from __future__ import annotations

import topics
from agent_tools._base import ToolDef


def _tag_object(params: dict, chat_id: int, user_id: int) -> dict:
    object_kind = params["object_kind"]
    object_id = params["object_id"]
    tag_id = params["tag_id"]
    if object_kind == "task":
        topics.tag_task(user_id, object_id, [tag_id], tag_source="agent")
    elif object_kind == "input":
        topics.tag_entry(user_id, object_id, [tag_id], tag_source="agent")
    else:
        return {"error": f"Unsupported object_kind: {object_kind}"}
    return {
        "id": f"{object_kind}:{object_id}:{tag_id}",
        "object_kind": object_kind,
        "object_id": object_id,
        "tag_id": tag_id,
    }


def _batch_tag_objects(params: dict, chat_id: int, user_id: int) -> dict:
    tagged = []
    errors = []
    for item in params["items"]:
        result = _tag_object(item, chat_id, user_id)
        if "error" in result:
            errors.append({"input": item, "error": result["error"]})
        else:
            tagged.append(result)
    return {
        "affected_ids": [r["id"] for r in tagged],
        "tagged": tagged,
        "errors": errors,
        "count": len(tagged),
    }


def _untag_object(params: dict, chat_id: int, user_id: int) -> dict:
    object_kind = params["object_kind"]
    object_id = params["object_id"]
    tag_id = params["tag_id"]
    if object_kind == "task":
        topics.untag_task(object_id, tag_id)
    elif object_kind == "input":
        topics.untag_entry(object_id, tag_id)
    else:
        return {"error": f"Unsupported object_kind: {object_kind}"}
    return {"object_kind": object_kind, "object_id": object_id, "tag_id": tag_id}


def _add_tag_link(params: dict, chat_id: int, user_id: int) -> dict:
    topics.add_tag_link(
        user_id,
        params["from_tag_id"],
        params["to_tag_id"],
        note=params.get("note"),
        source="agent",
    )
    return {"from_tag_id": params["from_tag_id"], "to_tag_id": params["to_tag_id"]}


def _remove_tag_link(params: dict, chat_id: int, user_id: int) -> dict:
    topics.remove_tag_link(params["from_tag_id"], params["to_tag_id"])
    return {"from_tag_id": params["from_tag_id"], "to_tag_id": params["to_tag_id"]}


def _get_tag_links(params: dict, chat_id: int, user_id: int) -> dict:
    links = topics.get_tag_links(params["tag_id"])
    return {"links": links, "count": len(links)}


TOOLS = [
    ToolDef(
        name="tag_object",
        description="Add a tag (topic or entity) to a task or journal entry.",
        input_schema={
            "type": "object",
            "properties": {
                "object_kind": {"type": "string", "enum": ["task", "input"], "description": "Type of object to tag"},
                "object_id": {"type": "string", "description": "ID of the task or entry"},
                "tag_id": {"type": "integer", "description": "ID of the tag to apply"},
            },
            "required": ["object_kind", "object_id", "tag_id"],
        },
        handler=_tag_object,
        mutates=True,
        object_kind="object_tag",
    ),
    ToolDef(
        name="batch_tag_objects",
        description="Add tags to multiple tasks or journal entries in one call. Use this for bulk tagging work after searching for the relevant object and tag IDs.",
        input_schema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Tagging operations to perform.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "object_kind": {"type": "string", "enum": ["task", "input"]},
                            "object_id": {"type": "string", "description": "ID of the task or entry"},
                            "tag_id": {"type": "integer", "description": "ID of the tag to apply"},
                        },
                        "required": ["object_kind", "object_id", "tag_id"],
                    },
                },
            },
            "required": ["items"],
        },
        handler=_batch_tag_objects,
        mutates=True,
        object_kind="object_tag",
    ),
    ToolDef(
        name="untag_object",
        description="Remove a tag from a task or journal entry.",
        input_schema={
            "type": "object",
            "properties": {
                "object_kind": {"type": "string", "enum": ["task", "input"]},
                "object_id": {"type": "string"},
                "tag_id": {"type": "integer"},
            },
            "required": ["object_kind", "object_id", "tag_id"],
        },
        handler=_untag_object,
        mutates=True,
        object_kind="object_tag",
    ),
    ToolDef(
        name="add_tag_link",
        description="Create a backlink between two tags (topics or entities). Links are directional.",
        input_schema={
            "type": "object",
            "properties": {
                "from_tag_id": {"type": "integer"},
                "to_tag_id": {"type": "integer"},
                "note": {"type": "string", "description": "Optional note about the relationship"},
            },
            "required": ["from_tag_id", "to_tag_id"],
        },
        handler=_add_tag_link,
        mutates=True,
        object_kind="tag_link",
    ),
    ToolDef(
        name="remove_tag_link",
        description="Remove a backlink between two tags (removes in both directions).",
        input_schema={
            "type": "object",
            "properties": {
                "from_tag_id": {"type": "integer"},
                "to_tag_id": {"type": "integer"},
            },
            "required": ["from_tag_id", "to_tag_id"],
        },
        handler=_remove_tag_link,
        mutates=True,
        object_kind="tag_link",
    ),
    ToolDef(
        name="get_tag_links",
        description="Get all backlinks for a tag (both directions).",
        input_schema={
            "type": "object",
            "properties": {
                "tag_id": {"type": "integer"},
            },
            "required": ["tag_id"],
        },
        handler=_get_tag_links,
    ),
]
