"""OpenAPI-backed Custom GPT Actions facade over VoiceJournal agent tools."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import agent_tools
from integration_auth import (
    BASE_URL,
    GPT_ACTIONS_RESOURCE,
    SUPPORTED_SCOPES,
    default_scope,
    get_bearer_auth_context,
    required_scope,
    title_for_tool,
)

gpt_actions_bp = Blueprint("gpt_actions", __name__, url_prefix="/gpt-actions")

_EXPOSED_ACTION_TOOLS = {
    "search_entries",
    "list_entries",
    "get_entry",
    "batch_get_entries",
    "search_tasks",
    "get_task",
    "batch_get_tasks",
    "create_task",
    "update_task",
    "batch_update_tasks",
    "delete_task",
    "batch_delete_tasks",
    "search_tags",
    "list_tags",
    "get_tag",
    "batch_get_tags",
    "create_tag",
    "batch_create_tags",
    "update_tag",
    "batch_update_tags",
    "merge_tags",
    "batch_merge_tags",
    "tag_object",
    "batch_tag_objects",
    "get_tag_notes",
    "batch_get_tag_notes",
    "get_setting",
    "batch_get_settings",
}


def _unauthorized():
    return (
        jsonify({"error": "unauthorized"}),
        401,
        {"WWW-Authenticate": f'Bearer realm="voicejournal" scope="{default_scope()}"'},
    )


def _auth_context():
    return get_bearer_auth_context(request.headers.get("Authorization", ""), GPT_ACTIONS_RESOURCE)


def _call_tool(tool_name: str, arguments: dict, auth_context: dict) -> tuple[dict, int]:
    tool_def = agent_tools.get_tool(tool_name, auth_context["user"]["id"])
    if not tool_def:
        return {"error": f"Unknown tool: {tool_name}"}, 404

    scope = required_scope(tool_def.name, tool_def.mutates)
    if scope not in auth_context["scopes"]:
        return {"error": f"Missing required scope: {scope}"}, 403

    try:
        result = tool_def.handler(arguments or {}, None, auth_context["user"]["id"])
    except Exception as exc:
        result = {"error": str(exc)}
    return result, 500 if isinstance(result, dict) and "error" in result else 200


@gpt_actions_bp.route("/openapi.json", methods=["GET"])
def openapi_schema():
    return jsonify(_build_openapi_schema())


@gpt_actions_bp.route("/batch", methods=["POST"])
def batch_call():
    auth = _auth_context()
    if not auth:
        return _unauthorized()

    body = request.get_json(silent=True) or {}
    operations = body.get("operations") or []
    if not isinstance(operations, list) or len(operations) > 25:
        return jsonify({"error": "operations must be an array of at most 25 items"}), 400

    results = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            results.append({"index": index, "error": "operation must be an object"})
            continue
        result, status = _call_tool(operation.get("tool_name", ""), operation.get("arguments") or {}, auth)
        results.append({
            "index": index,
            "tool_name": operation.get("tool_name"),
            "status": status,
            "result": result,
        })
    return jsonify({"results": results, "count": len(results)})


@gpt_actions_bp.route("/<tool_name>", methods=["POST"])
def call_tool(tool_name: str):
    auth = _auth_context()
    if not auth:
        return _unauthorized()

    result, status = _call_tool(tool_name, request.get_json(silent=True) or {}, auth)
    return jsonify(result), status


def _build_openapi_schema() -> dict:
    paths = {
        "/gpt-actions/batch": {
            "post": {
                "operationId": "batch_call",
                "summary": "Call multiple VoiceJournal tools in one request",
                "description": "Batch up to 25 read or write operations. Prefer dedicated batch lookup tools for multiple gets.",
                "security": [{"OAuth2": SUPPORTED_SCOPES}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "operations": {
                                        "type": "array",
                                        "maxItems": 25,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "tool_name": {"type": "string"},
                                                "arguments": {"type": "object"},
                                            },
                                            "required": ["tool_name"],
                                        },
                                    },
                                },
                                "required": ["operations"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Batch results"}},
            }
        }
    }

    for tool_def in agent_tools.get_all_tools():
        if tool_def.name not in _EXPOSED_ACTION_TOOLS:
            continue
        scope = required_scope(tool_def.name, tool_def.mutates)
        paths[f"/gpt-actions/{tool_def.name}"] = {
            "post": {
                "operationId": tool_def.name,
                "summary": title_for_tool(tool_def.name),
                "description": tool_def.description,
                "security": [{"OAuth2": [scope]}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": tool_def.input_schema,
                        }
                    },
                },
                "responses": {
                    "200": {"description": "Tool result"},
                    "401": {"description": "Unauthorized"},
                    "403": {"description": "Missing required OAuth scope"},
                },
            }
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "VoiceJournal Actions",
            "version": "1.0.0",
            "description": "Search, read, organize, and update VoiceJournal entries, tasks, topics, entities, and settings.",
        },
        "servers": [{"url": BASE_URL}],
        "security": [{"OAuth2": SUPPORTED_SCOPES}],
        "paths": paths,
        "components": {
            "schemas": {},
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{BASE_URL}/oauth/authorize",
                            "tokenUrl": f"{BASE_URL}/oauth/token",
                            "scopes": {scope: scope for scope in SUPPORTED_SCOPES},
                        }
                    },
                }
            }
        },
    }
