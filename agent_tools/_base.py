"""Base types and execution wrapper for agent tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import topics


@dataclass
class ToolDef:
    """A single tool the chat agent can invoke."""

    name: str
    description: str
    input_schema: dict  # JSON Schema (type: object)
    handler: Callable[[dict, int, int], dict]  # (params, chat_id, user_id) -> result
    mutates: bool = False
    object_kind: str | None = None  # For chat_actions: "task", "tag", etc.


def execute_tool(tool_def: ToolDef, params: dict, chat_id: int, user_id: int) -> dict:
    """Run a tool handler and log mutations to chat_actions."""
    result = tool_def.handler(params, chat_id, user_id)
    if tool_def.mutates:
        # Determine object_id from result — tools should include "id" when applicable
        object_id = result.get("id")
        # For batch operations, log each affected ID
        affected_ids = result.get("affected_ids")
        if affected_ids:
            for oid in affected_ids:
                topics.log_chat_action(
                    chat_id=chat_id,
                    action=f"agent_{tool_def.name}",
                    object_kind=tool_def.object_kind,
                    object_id=str(oid),
                    payload={"input": params},
                )
        else:
            topics.log_chat_action(
                chat_id=chat_id,
                action=f"agent_{tool_def.name}",
                object_kind=tool_def.object_kind,
                object_id=str(object_id) if object_id else None,
                payload={"input": params, "result": result},
            )
    return result


def tool_to_schema(tool_def: ToolDef) -> dict:
    """Convert a ToolDef to the Claude API tools array format."""
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "input_schema": tool_def.input_schema,
    }
