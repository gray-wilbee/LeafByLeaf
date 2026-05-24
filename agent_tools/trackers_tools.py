"""Agent tools for Tracker CRUD, data entry, and questions."""

from __future__ import annotations

import json
import trackers as trackers_db
import tasks as tasks_db
from agent_tools._base import ToolDef


def _create_tracker(params: dict, chat_id: int, user_id: int) -> dict:
    try:
        tracker_id = trackers_db.create_tracker(
            user_id,
            name=params["name"],
            description=params.get("description"),
            cadence=params.get("cadence", "daily"),
            timezone_str=params.get("timezone"),
            start_date=params.get("start_date"),
        )
    except trackers_db.TrackerValidationError as e:
        return {"error": str(e)}
    # Add fields if provided
    for i, f in enumerate(params.get("fields") or []):
        field_name = f.get("name") or ""
        field_key = f.get("field_key") or field_name.lower().replace(" ", "_")
        field_type = f.get("field_type", "text")
        options_json = json.dumps(f["options"]) if f.get("options") else None
        try:
            linked_task_id = f.get("linked_task_id")
            recurring_task = f.get("recurring_task") or {}
            if recurring_task.get("title") and recurring_task.get("recurrence_rule"):
                linked_task_id = tasks_db.create_task(
                    user_id,
                    title=recurring_task["title"],
                    status="open",
                    source="tracker",
                    recurrence_rule=recurring_task["recurrence_rule"],
                )
            trackers_db.add_field(
                user_id, tracker_id,
                name=field_name,
                field_key=field_key,
                field_type=field_type,
                description=f.get("description"),
                required=f.get("required", False),
                options_json=options_json,
                unit=f.get("unit"),
                min_value=f.get("min_value"),
                max_value=f.get("max_value"),
                inference_policy=f.get("inference_policy", "ask_if_missing"),
                sort_order=i,
                ai_explanation=f.get("ai_explanation"),
                linked_task_id=linked_task_id,
            )
        except trackers_db.TrackerValidationError as e:
            return {"error": str(e)}
    return {
        "id": tracker_id,
        "name": params["name"],
        "cadence": params.get("cadence", "daily"),
        "url": f"/trackers/{tracker_id}",
    }


def _list_trackers(params: dict, chat_id: int, user_id: int) -> dict:
    include_archived = params.get("include_archived", False)
    all_trackers = trackers_db.list_trackers(user_id, include_archived=include_archived)
    slim = [{
        "id": t["id"],
        "name": t["name"],
        "cadence": t["cadence"],
        "description": t.get("description") or "",
        "archived": bool(t.get("archived_at")),
    } for t in all_trackers]
    return {"trackers": slim, "count": len(slim)}


def _get_tracker_data(params: dict, chat_id: int, user_id: int) -> dict:
    tracker_id = params["tracker_id"]
    tracker = trackers_db.get_tracker(user_id, tracker_id)
    if not tracker:
        return {"error": f"Tracker {tracker_id} not found"}
    fields = trackers_db.list_fields(tracker_id)
    limit = params.get("limit", 30)
    rows = trackers_db.list_rows(user_id, tracker_id, limit=limit)
    row_ids = [r["id"] for r in rows]
    values_map = trackers_db.get_values_for_rows(row_ids)

    # Build human-readable row data
    rows_out = []
    for row in rows:
        row_vals = values_map.get(row["id"], {})
        entry = {"period_start": row["period_start"], "status": row["status"], "values": {}}
        for f in fields:
            v = row_vals.get(f["id"])
            if v and v.get("value_json"):
                try:
                    entry["values"][f["field_key"]] = json.loads(v["value_json"])
                except Exception:
                    entry["values"][f["field_key"]] = v["value_json"]
        rows_out.append(entry)

    return {
        "id": tracker["id"],
        "name": tracker["name"],
        "cadence": tracker["cadence"],
        "fields": [{"id": f["id"], "name": f["name"], "field_key": f["field_key"], "field_type": f["field_type"]} for f in fields],
        "rows": rows_out,
        "row_count": len(rows_out),
    }


def _update_tracker_row(params: dict, chat_id: int, user_id: int) -> dict:
    tracker_id = params["tracker_id"]
    period_start = params["period_start"]
    values = params.get("values") or {}

    tracker = trackers_db.get_tracker(user_id, tracker_id)
    if not tracker:
        return {"error": f"Tracker {tracker_id} not found"}
    fields = trackers_db.list_fields(tracker_id)
    field_map = {f["field_key"]: f["id"] for f in fields}

    period_end = params.get("period_end")
    if not period_end:
        try:
            period_start, period_end = trackers_db.derive_period_bounds(period_start, tracker["cadence"])
        except (trackers_db.TrackerValidationError, ValueError) as e:
            return {"error": str(e)}

    try:
        row = trackers_db.get_or_create_row(user_id, tracker_id, period_start, period_end)
    except trackers_db.TrackerValidationError as e:
        return {"error": str(e)}
    row_id = row["id"]

    updated = []
    for field_key, raw_value in values.items():
        field_id = field_map.get(field_key)
        if not field_id:
            continue
        field = next((f for f in fields if f["id"] == field_id), None)
        try:
            value = trackers_db.coerce_value_for_field(field, raw_value)
            trackers_db.set_value(
                user_id, row_id, field_id,
                json.dumps(value),
                source="agent",
                confidence="user_confirmed",
                source_session_id=str(chat_id),
            )
        except (trackers_db.TrackerValidationError, ValueError, TypeError) as e:
            return {"error": str(e)}
        updated.append(field_key)

    return {"ok": True, "row_id": row_id, "period_start": period_start, "updated_fields": updated}


def _answer_tracker_question(params: dict, chat_id: int, user_id: int) -> dict:
    trackers_db.answer_question(user_id, params["question_id"])
    return {"ok": True}


TOOLS = [
    ToolDef(
        name="create_tracker",
        description=(
            "Create a new custom tracker with optional fields. "
            "Use this when the user wants to start tracking something over time."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the tracker"},
                "description": {"type": "string", "description": "What is being tracked"},
                "cadence": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly", "ad_hoc"],
                    "description": "How often data is recorded (default: daily)",
                },
                "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "fields": {
                    "type": "array",
                    "description": "Fields to create on the tracker",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "field_key": {"type": "string", "description": "Snake_case key (auto-derived from name if omitted)"},
                            "field_type": {
                                "type": "string",
                                "enum": ["boolean", "number", "text", "scale", "time", "duration", "select", "multi_select"],
                            },
                            "required": {"type": "boolean"},
                            "unit": {"type": "string"},
                            "min_value": {"type": "number"},
                            "max_value": {"type": "number"},
                            "options": {"type": "array", "items": {"type": "string"}},
                            "ai_explanation": {
                                "type": "string",
                                "description": "Guidance for when AI should infer this field versus ask the user.",
                            },
                            "linked_task_id": {
                                "type": "string",
                                "description": "Existing recurring task id to compute this field from.",
                            },
                            "recurring_task": {
                                "type": "object",
                                "description": "New recurring task to create and link to this field.",
                                "properties": {
                                    "title": {"type": "string"},
                                    "recurrence_rule": {"type": "string"},
                                },
                            },
                            "inference_policy": {
                                "type": "string",
                                "enum": ["manual_only", "infer_when_explicit", "infer_when_likely", "system_computed", "ask_if_missing"],
                            },
                        },
                        "required": ["name", "field_type"],
                    },
                },
            },
            "required": ["name"],
        },
        handler=_create_tracker,
        mutates=True,
        object_kind="tracker",
    ),
    ToolDef(
        name="list_trackers",
        description="List the user's trackers.",
        input_schema={
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean", "description": "Include archived trackers (default false)"},
            },
            "required": [],
        },
        handler=_list_trackers,
        mutates=False,
        object_kind="tracker",
    ),
    ToolDef(
        name="get_tracker_data",
        description=(
            "Get tracker details including fields and recent row data with values. "
            "Use this to answer questions like 'how did I do this week?' or 'show me my habit data'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tracker_id": {"type": "string", "description": "8-char hex tracker ID"},
                "limit": {"type": "integer", "description": "Max rows to return (default 30)"},
            },
            "required": ["tracker_id"],
        },
        handler=_get_tracker_data,
        mutates=False,
        object_kind="tracker",
    ),
    ToolDef(
        name="update_tracker_row",
        description=(
            "Set field values for a tracker row (period). Creates the row if it doesn't exist. "
            "Use this when the user says things like 'I drank 48oz of water today' or 'I worked out twice this week'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tracker_id": {"type": "string", "description": "8-char hex tracker ID"},
                "period_start": {"type": "string", "description": "Period start date (YYYY-MM-DD)"},
                "period_end": {"type": "string", "description": "Period end date (YYYY-MM-DD, defaults to period_start)"},
                "values": {
                    "type": "object",
                    "description": "Map of field_key -> value. Value type matches field_type.",
                    "additionalProperties": True,
                },
            },
            "required": ["tracker_id", "period_start", "values"],
        },
        handler=_update_tracker_row,
        mutates=True,
        object_kind="tracker",
    ),
    ToolDef(
        name="answer_tracker_question",
        description="Mark a tracker follow-up question as answered.",
        input_schema={
            "type": "object",
            "properties": {
                "question_id": {"type": "string", "description": "8-char hex question ID"},
            },
            "required": ["question_id"],
        },
        handler=_answer_tracker_question,
        mutates=True,
        object_kind="tracker",
    ),
]
