"""Agent tools for Decision Log CRUD and tagging."""

from __future__ import annotations

import decisions as decisions_db
import topics
from agent_tools._base import ToolDef


def _create_decision_log_item(params: dict, chat_id: int, user_id: int) -> dict:
    try:
        item_id = decisions_db.create_item(
            user_id,
            item_type=params["item_type"],
            title=params["title"],
            content=params.get("content"),
            rationale=params.get("rationale"),
            status=params.get("status", "open"),
            review_status="accepted",
            source="agent",
            source_session_id=str(chat_id),
            confidence=params.get("confidence"),
            review_at=params.get("review_at"),
            reject_duplicates=True,
        )
    except decisions_db.DuplicateDecisionError:
        return {"error": "duplicate decision log item"}
    except decisions_db.DecisionValidationError as e:
        return {"error": str(e)}
    tag_ids = params.get("tag_ids") or []
    if tag_ids:
        decisions_db.tag_decision(user_id, item_id, tag_ids, tag_source="agent")
    return {"id": item_id, "title": params["title"], "item_type": params["item_type"]}


def _list_decision_log_items(params: dict, chat_id: int, user_id: int) -> dict:
    item_type = params.get("item_type")
    status = params.get("status")
    review_status = params.get("review_status", "accepted")
    tag_ids = params.get("tag_ids")

    if tag_ids:
        items = decisions_db.list_for_tags(user_id, tag_ids, include_children=True)
        if item_type:
            items = [i for i in items if i["item_type"] == item_type]
        if status:
            items = [i for i in items if i["status"] == status]
    else:
        items = decisions_db.list_items(user_id, item_type=item_type, status=status,
                                        review_status=review_status)

    limit = params.get("limit", 30)
    items = items[:limit]
    tag_map = decisions_db.get_decision_tag_map(user_id, [i["id"] for i in items])
    for item in items:
        item["tags"] = [t["name"] for t in tag_map.get(item["id"], [])]
    slim = [{
        "id": i["id"],
        "item_type": i["item_type"],
        "status": i["status"],
        "title": i["title"],
        "content": (i.get("content") or "")[:300],
        "rationale": (i.get("rationale") or "")[:200],
        "tags": i["tags"],
        "created_at": i["created_at"][:10],
    } for i in items]
    return {"items": slim, "count": len(slim)}


def _get_decision_log_item(params: dict, chat_id: int, user_id: int) -> dict:
    item = decisions_db.get_item(user_id, params["item_id"])
    if not item:
        return {"error": f"Decision log item {params['item_id']} not found"}
    item["tags"] = decisions_db.get_tags_for_decision(item["id"])
    return item


def _update_decision_log_item(params: dict, chat_id: int, user_id: int) -> dict:
    item_id = params["item_id"]
    if not decisions_db.get_item(user_id, item_id):
        return {"error": f"Decision log item {item_id} not found"}
    try:
        decisions_db.update_item(user_id, item_id, **{
            k: v for k, v in params.items() if k != "item_id"
        })
    except (decisions_db.DecisionValidationError, decisions_db.DuplicateDecisionError) as e:
        return {"error": str(e)}
    return {"ok": True, "id": item_id}


def _link_decision_to_topic(params: dict, chat_id: int, user_id: int) -> dict:
    item_id = params["item_id"]
    tag_ids = params["tag_ids"]
    if not decisions_db.get_item(user_id, item_id):
        return {"error": f"Decision log item {item_id} not found"}
    decisions_db.tag_decision(user_id, item_id, tag_ids, tag_source="agent")
    return {"ok": True, "item_id": item_id, "linked_tag_ids": tag_ids}


def _dismiss_decision_suggestion(params: dict, chat_id: int, user_id: int) -> dict:
    decisions_db.dismiss_suggestion(user_id, params["item_id"])
    return {"ok": True}


TOOLS = [
    ToolDef(
        name="create_decision_log_item",
        description=(
            "Create a Decision Log item — a decision, open question, or revisit-later note. "
            "Use this to record conclusions, unresolved questions, or deferred tradeoffs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_type": {
                    "type": "string",
                    "enum": ["decision", "open_question", "revisit_later"],
                    "description": "Type of item to create",
                },
                "title": {"type": "string", "description": "Short summary of the decision or question"},
                "content": {"type": "string", "description": "Full text or context"},
                "rationale": {"type": "string", "description": "Why this was decided or what makes it open"},
                "status": {
                    "type": "string",
                    "enum": ["open", "decided", "deferred", "reversed"],
                    "description": "Current status (defaults to 'open')",
                },
                "tag_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Topic/entity tag IDs to associate this item with",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Confidence level in this decision",
                },
                "review_at": {"type": "string", "description": "Date to revisit (YYYY-MM-DD)"},
            },
            "required": ["item_type", "title"],
        },
        handler=_create_decision_log_item,
        mutates=True,
        object_kind="decision",
    ),
    ToolDef(
        name="list_decision_log_items",
        description=(
            "List Decision Log items. Can filter by type, status, and topic tags."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_type": {
                    "type": "string",
                    "enum": ["decision", "open_question", "revisit_later"],
                    "description": "Filter by item type",
                },
                "status": {
                    "type": "string",
                    "enum": ["open", "decided", "deferred", "reversed"],
                    "description": "Filter by status",
                },
                "review_status": {
                    "type": "string",
                    "enum": ["accepted", "suggested", "dismissed"],
                    "description": "Filter by review status (defaults to 'accepted')",
                },
                "tag_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Filter to items tagged with these topic/entity IDs (includes children)",
                },
                "limit": {"type": "integer", "description": "Max results (default 30)"},
            },
            "required": [],
        },
        handler=_list_decision_log_items,
        mutates=False,
        object_kind="decision",
    ),
    ToolDef(
        name="get_decision_log_item",
        description="Get full details of a single Decision Log item by ID.",
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "8-char hex decision log item ID"},
            },
            "required": ["item_id"],
        },
        handler=_get_decision_log_item,
        mutates=False,
        object_kind="decision",
    ),
    ToolDef(
        name="update_decision_log_item",
        description="Update fields of an existing Decision Log item.",
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "8-char hex decision log item ID"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "rationale": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "decided", "deferred", "reversed"]},
                "item_type": {"type": "string", "enum": ["decision", "open_question", "revisit_later"]},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "review_at": {"type": "string"},
            },
            "required": ["item_id"],
        },
        handler=_update_decision_log_item,
        mutates=True,
        object_kind="decision",
    ),
    ToolDef(
        name="link_decision_to_topic",
        description="Tag a Decision Log item with one or more topic/entity IDs.",
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "8-char hex decision log item ID"},
                "tag_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Topic/entity IDs to link",
                },
            },
            "required": ["item_id", "tag_ids"],
        },
        handler=_link_decision_to_topic,
        mutates=True,
        object_kind="decision",
    ),
    ToolDef(
        name="dismiss_decision_suggestion",
        description="Dismiss a suggested (AI-generated) Decision Log item that should not be kept.",
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "8-char hex decision log item ID"},
            },
            "required": ["item_id"],
        },
        handler=_dismiss_decision_suggestion,
        mutates=True,
        object_kind="decision",
    ),
]
