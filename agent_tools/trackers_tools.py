"""Agent tools for Tracker CRUD and data entry."""

from __future__ import annotations

import json
import trackers as trackers_db
from agent_tools._base import ToolDef


def _create_tracker(params: dict, chat_id: int, user_id: int) -> dict:
    try:
        tracker_id = trackers_db.create_tracker(
            user_id,
            name=params["name"],
            type=params.get("type", "yes_no"),
            frequency=params.get("frequency", "Daily"),
            capture_instructions=params.get("capture_instructions"),
            ai_commentary_instructions=params.get("ai_commentary_instructions"),
        )
    except trackers_db.TrackerError as e:
        return {"error": str(e)}
    return {
        "id": tracker_id,
        "name": params["name"],
        "url": f"/trackers/{tracker_id}",
    }


def _list_trackers(params: dict, chat_id: int, user_id: int) -> dict:
    include_archived = params.get("include_archived", False)
    all_trackers = trackers_db.list_trackers(user_id, include_archived=include_archived)
    slim = [{
        "id": t["id"],
        "name": t["name"],
        "type": t["type"],
        "frequency": t["frequency"],
        "archived": bool(t.get("archived_at")),
    } for t in all_trackers]
    return {"trackers": slim, "count": len(slim)}


def _get_tracker_data(params: dict, chat_id: int, user_id: int) -> dict:
    tracker_id = params["tracker_id"]
    tracker = trackers_db.get_tracker(user_id, tracker_id)
    if not tracker:
        return {"error": f"Tracker {tracker_id} not found"}
    limit = params.get("limit", 30)
    entries = trackers_db.list_entries(user_id, tracker_id, limit=limit)
    entries_out = []
    for e in entries:
        val = None
        if e.get("value_json") is not None:
            try:
                val = json.loads(e["value_json"])
            except Exception:
                val = e["value_json"]
        entries_out.append({
            "date": e["entry_date"],
            "value": val,
            "skipped": bool(e["skipped"]),
            "source": e["source"],
        })
    return {
        "id": tracker["id"],
        "name": tracker["name"],
        "type": tracker["type"],
        "frequency": tracker["frequency"],
        "entries": entries_out,
        "entry_count": len(entries_out),
    }


def _set_tracker_entry(params: dict, chat_id: int, user_id: int) -> dict:
    tracker_id = params["tracker_id"]
    tracker = trackers_db.get_tracker(user_id, tracker_id)
    if not tracker:
        return {"error": f"Tracker {tracker_id} not found"}
    entry_date = params["entry_date"]
    raw_value = params.get("value")
    value_json = json.dumps(raw_value) if raw_value is not None else None
    eid = trackers_db.upsert_entry(user_id, tracker_id, entry_date, value_json, source="agent")
    return {"ok": True, "entry_id": eid, "entry_date": entry_date, "value": raw_value}


TOOLS = [
    ToolDef(
        name="create_tracker",
        description="Create a new tracker. Use when the user wants to start tracking something over time.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the tracker"},
                "type": {
                    "type": "string",
                    "enum": ["yes_no", "number", "text"],
                    "description": "Value type: yes_no for habits, number for quantities, text for notes",
                },
                "frequency": {"type": "string", "description": "How often to capture (e.g. Daily, Every workday)"},
                "capture_instructions": {"type": "string", "description": "Instructions for AI auto-capture"},
                "ai_commentary_instructions": {"type": "string", "description": "Instructions for AI commentary (optional)"},
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
        description="Get a tracker's recent entries. Use to answer questions about habit data.",
        input_schema={
            "type": "object",
            "properties": {
                "tracker_id": {"type": "string", "description": "Tracker ID"},
                "limit": {"type": "integer", "description": "Max entries to return (default 30)"},
            },
            "required": ["tracker_id"],
        },
        handler=_get_tracker_data,
        mutates=False,
        object_kind="tracker",
    ),
    ToolDef(
        name="set_tracker_entry",
        description="Set the value for a tracker on a specific date.",
        input_schema={
            "type": "object",
            "properties": {
                "tracker_id": {"type": "string", "description": "Tracker ID"},
                "entry_date": {"type": "string", "description": "Date (YYYY-MM-DD)"},
                "value": {"description": "Value to record (boolean, number, or string depending on tracker type)"},
            },
            "required": ["tracker_id", "entry_date", "value"],
        },
        handler=_set_tracker_entry,
        mutates=True,
        object_kind="tracker",
    ),
]
