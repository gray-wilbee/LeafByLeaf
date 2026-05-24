"""Agent tools for reading and updating app settings."""

from __future__ import annotations

import topics
from agent_tools._base import ToolDef


def _get_setting(params: dict, chat_id: int, user_id: int) -> dict:
    key = params["key"]
    value = topics.get_setting(user_id, key)
    return {"key": key, "value": value}


def _set_setting(params: dict, chat_id: int, user_id: int) -> dict:
    key = params["key"]
    value = params["value"]
    topics.set_setting(user_id, key, value)
    return {"key": key, "value": value}


def _list_settings(params: dict, chat_id: int, user_id: int) -> dict:
    settings = topics.get_all_settings(user_id)
    return {"settings": settings}


TOOLS = [
    ToolDef(
        name="get_setting",
        description="Get a single setting value by key. Known keys: user_profile, sort_instructions.",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
            },
            "required": ["key"],
        },
        handler=_get_setting,
    ),
    ToolDef(
        name="set_setting",
        description="Set a setting value. Known keys: user_profile (background context for AI extraction), sort_instructions (custom extraction rules).",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
        handler=_set_setting,
        mutates=True,
        object_kind="setting",
    ),
    ToolDef(
        name="list_settings",
        description="List all current settings.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_list_settings,
    ),
]
