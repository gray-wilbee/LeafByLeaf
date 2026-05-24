"""Agent tool registry — auto-discovers tool modules and provides dispatch."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from agent_tools._base import ToolDef, execute_tool, tool_to_schema
import users

_registry: dict[str, ToolDef] = {}
_loaded = False


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    pkg_dir = Path(__file__).parent
    for info in pkgutil.iter_modules([str(pkg_dir)]):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"agent_tools.{info.name}")
        tools_list = getattr(mod, "TOOLS", [])
        for t in tools_list:
            _registry[t.name] = t
    _loaded = True


def _is_allowed_for_user(tool_def: ToolDef, user_id: int | None) -> bool:
    if tool_def.object_kind != "decision":
        return True
    return bool(user_id and (int(user_id) == 1 or users.is_admin(user_id)))


def _filter_tools_for_user(tools: list[ToolDef], user_id: int | None) -> list[ToolDef]:
    if user_id is None:
        return [t for t in tools if t.object_kind != "decision"]
    return [t for t in tools if _is_allowed_for_user(t, user_id)]


def get_all_tools(user_id: int | None = None) -> list[ToolDef]:
    _load_all()
    return _filter_tools_for_user(list(_registry.values()), user_id)


def get_tools_schema(user_id: int | None = None) -> list[dict]:
    """Return the `tools` array for the Claude API payload."""
    _load_all()
    return [tool_to_schema(t) for t in _filter_tools_for_user(list(_registry.values()), user_id)]


def get_tool(name: str, user_id: int | None = None) -> ToolDef | None:
    """Return a ToolDef by name, or None if not found."""
    _load_all()
    tool_def = _registry.get(name)
    if not tool_def or not _is_allowed_for_user(tool_def, user_id):
        return None
    return tool_def


def dispatch(tool_name: str, tool_input: dict, chat_id: int, user_id: int) -> dict:
    """Execute a tool by name. Returns result dict or error dict."""
    _load_all()
    tool_def = _registry.get(tool_name)
    if not tool_def:
        return {"error": f"Unknown tool: {tool_name}"}
    if not _is_allowed_for_user(tool_def, user_id):
        return {"error": f"Forbidden tool: {tool_name}"}
    try:
        return execute_tool(tool_def, tool_input, chat_id, user_id)
    except Exception as e:
        return {"error": str(e)}
