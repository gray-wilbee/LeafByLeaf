"""Agent tools for journal entry search and access."""

from __future__ import annotations

import journal
from agent_tools._base import ToolDef


def _search_entries(params: dict, chat_id: int, user_id: int) -> dict:
    query = params.get("query")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    limit = params.get("limit", 20)
    results = journal.search_entries(
        user_id, query=query, date_from=date_from, date_to=date_to, limit=limit
    )
    return {"entries": results, "count": len(results)}


def _get_entry(params: dict, chat_id: int, user_id: int) -> dict:
    entry = journal.get_entry_by_id(user_id, params["entry_id"])
    if not entry:
        return {"error": f"Entry {params['entry_id']} not found"}
    return entry


def _list_entries(params: dict, chat_id: int, user_id: int) -> dict:
    limit = params.get("limit", 20)
    offset = params.get("offset", 0)
    all_entries = journal.list_entries(user_id)
    page = all_entries[offset:offset + limit]
    # Slim down — don't send full content in list view
    slim = []
    for e in page:
        slim.append({
            "id": e["id"],
            "date": e["date"],
            "time": e["time"],
            "preview": (e.get("content") or "")[:200],
        })
    return {"entries": slim, "count": len(slim), "total": len(all_entries)}


TOOLS = [
    ToolDef(
        name="search_entries",
        description="Search journal entries by content text, optionally filtered by date range. Returns matching entries with IDs, dates, and preview text.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword to match against entry content"},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": [],
        },
        handler=_search_entries,
    ),
    ToolDef(
        name="get_entry",
        description="Get the full content of a single journal entry by its 8-char hex ID.",
        input_schema={
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "8-char hex entry ID"},
            },
            "required": ["entry_id"],
        },
        handler=_get_entry,
    ),
    ToolDef(
        name="list_entries",
        description="List journal entries in reverse chronological order with pagination. Returns preview text, not full content.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of entries per page (default 20)", "default": 20},
                "offset": {"type": "integer", "description": "Number of entries to skip (default 0)", "default": 0},
            },
            "required": [],
        },
        handler=_list_entries,
    ),
]
