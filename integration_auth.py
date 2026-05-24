"""Shared auth, scope, and tool metadata helpers for external integrations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import db
import users

BASE_URL = "https://journal.wilbeevibes.com"
MCP_RESOURCE = f"{BASE_URL}/mcp"
GPT_ACTIONS_RESOURCE = f"{BASE_URL}/gpt-actions"
SUPPORTED_RESOURCES = {MCP_RESOURCE, GPT_ACTIONS_RESOURCE}
SUPPORTED_SCOPES = [
    "journal.read",
    "tasks.read",
    "tasks.write",
    "tags.read",
    "tags.write",
    "settings.read",
    "settings.write",
]
SUPPORTED_SCOPES_SET = set(SUPPORTED_SCOPES)
DESTRUCTIVE_TOOL_PREFIXES = ("delete_", "batch_delete_", "merge_", "batch_merge_", "remove_", "untag_")


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def default_scope() -> str:
    return " ".join(SUPPORTED_SCOPES)


def validate_scope(scope: str) -> str | None:
    requested = scope.split() if scope else SUPPORTED_SCOPES
    unsupported = [s for s in requested if s not in SUPPORTED_SCOPES_SET]
    if unsupported:
        return None
    return " ".join(dict.fromkeys(requested))


def validate_resource(resource: str, default: str = MCP_RESOURCE) -> str | None:
    if not resource:
        return default
    return resource if resource in SUPPORTED_RESOURCES else None


def get_bearer_auth_context(auth_header: str, resource: str | None = None) -> dict | None:
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    token_hash = hash_secret(token)
    now_iso = datetime.now(timezone.utc).isoformat()

    with db.get_db() as conn:
        row = conn.execute(
            """SELECT u.*, ot.scope, ot.resource FROM oauth_tokens ot
               JOIN users u ON u.id = ot.user_id
               WHERE ot.token = ?
                 AND ot.revoked_at IS NULL
                 AND (ot.expires_at IS NULL OR ot.expires_at > ?)
                 AND (? IS NULL OR ot.resource IS NULL OR ot.resource = ?)
                 AND u.disabled_at IS NULL""",
            (token_hash, now_iso, resource, resource),
        ).fetchone()
    if row:
        row_dict = dict(row)
        scopes = set((row_dict.pop("scope") or default_scope()).split())
        token_resource = row_dict.pop("resource", None)
        return {"user": row_dict, "scopes": scopes, "resource": token_resource, "auth_type": "oauth"}

    user = users.get_by_api_key(token)
    if user:
        return {"user": user, "scopes": SUPPORTED_SCOPES_SET, "resource": resource, "auth_type": "api_key"}
    return None


def title_for_tool(name: str) -> str:
    return name.replace("_", " ").title()


def required_scope(name: str, mutates: bool) -> str:
    if name.endswith("_setting") or name.endswith("_settings") or "setting" in name:
        return "settings.write" if mutates else "settings.read"
    if name.endswith("_task") or name.endswith("_tasks") or "task" in name:
        return "tasks.write" if mutates else "tasks.read"
    if name.endswith("_entry") or name.endswith("_entries"):
        return "journal.read"
    return "tags.write" if mutates else "tags.read"


def tool_annotations(name: str, mutates: bool) -> dict[str, Any]:
    return {
        "readOnlyHint": not mutates,
        "destructiveHint": bool(mutates and name.startswith(DESTRUCTIVE_TOOL_PREFIXES)),
    }


def security_schemes(scope: str) -> list[dict[str, Any]]:
    return [{"type": "oauth2", "scopes": [scope]}]


def summarize_result(tool_name: str, result: dict) -> str:
    if "error" in result:
        return str(result["error"])
    count = result.get("count")
    for key in ("entries", "tasks", "tags", "notes", "settings", "results", "links"):
        if key in result:
            if count is None and isinstance(result[key], list):
                count = len(result[key])
            return f"{title_for_tool(tool_name)} returned {count} {key}.\n\n{result}"
    return str(result)
