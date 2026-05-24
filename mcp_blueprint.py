"""MCP HTTP endpoint for exposing VoiceJournal agent_tools via JSON-RPC 2.0."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

import agent_tools
from integration_auth import (
    BASE_URL,
    MCP_RESOURCE,
    default_scope,
    get_bearer_auth_context,
    required_scope,
    security_schemes,
    summarize_result,
    title_for_tool,
    tool_annotations,
)

mcp_bp = Blueprint("mcp", __name__)

_MCP_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "voicejournal", "version": "1.0"}


def _unauth_response():
    return (
        jsonify({"error": "unauthorized"}),
        401,
        {
            "WWW-Authenticate": (
                'Bearer realm="voicejournal" '
                f'resource_metadata="{BASE_URL}/.well-known/oauth-protected-resource" '
                f'scope="{default_scope()}"'
            )
        },
    )


def _ok(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _err(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _to_mcp_tool(tool_def) -> dict:
    scope = required_scope(tool_def.name, tool_def.mutates)
    schemes = security_schemes(scope)
    return {
        "name": tool_def.name,
        "title": title_for_tool(tool_def.name),
        "description": tool_def.description,
        "inputSchema": tool_def.input_schema,
        "securitySchemes": schemes,
        "annotations": tool_annotations(tool_def.name, tool_def.mutates),
        "_meta": {"securitySchemes": schemes},
    }


def _handle_rpc(body: dict, user: dict, scopes: set) -> dict:
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return _ok(rpc_id, {
            "protocolVersion": _MCP_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": _SERVER_INFO,
        })

    if method == "ping":
        return _ok(rpc_id, {})

    if method == "notifications/initialized":
        return _ok(rpc_id, {})

    if method == "tools/list":
        return _ok(rpc_id, {"tools": [_to_mcp_tool(t) for t in agent_tools.get_all_tools(user["id"])]})

    if method == "tools/call":
        tool_name = params.get("name")
        tool_input = params.get("arguments") or {}
        tool_def = agent_tools.get_tool(tool_name, user["id"])
        if not tool_def:
            return _err(rpc_id, -32602, f"Unknown tool: {tool_name}")

        scope = required_scope(tool_def.name, tool_def.mutates)
        if scope not in scopes:
            return _err(rpc_id, -32001, f"Missing required scope: {scope}")

        try:
            result = tool_def.handler(tool_input, None, user["id"])
        except Exception as exc:
            result = {"error": str(exc)}

        return _ok(rpc_id, {
            "content": [{"type": "text", "text": summarize_result(tool_def.name, result)}],
            "structuredContent": result if isinstance(result, dict) else {"result": result},
            "isError": isinstance(result, dict) and "error" in result,
        })

    return _err(rpc_id, -32601, "Method not found")


@mcp_bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    auth_context = get_bearer_auth_context(request.headers.get("Authorization", ""), MCP_RESOURCE)
    if not auth_context:
        return _unauth_response()

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "invalid JSON"}), 400

    if isinstance(body, list):
        responses = [_handle_rpc(item, auth_context["user"], auth_context["scopes"]) for item in body if isinstance(item, dict)]
        return jsonify(responses)

    return jsonify(_handle_rpc(body, auth_context["user"], auth_context["scopes"]))
