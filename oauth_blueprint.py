"""OAuth 2.1 authorization server for VoiceJournal external integrations."""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from flask import Blueprint, jsonify, redirect, render_template, request, session

import db
import users
from integration_auth import (
    BASE_URL,
    GPT_ACTIONS_RESOURCE,
    MCP_RESOURCE,
    SUPPORTED_SCOPES,
    default_scope,
    hash_secret,
    validate_resource,
    validate_scope,
)

oauth_bp = Blueprint("oauth", __name__)

_CODE_TTL_MINUTES = 5
_TOKEN_TTL_DAYS = 90


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@oauth_bp.route("/.well-known/oauth-authorization-server")
def oauth_metadata():
    return jsonify({
        "issuer": BASE_URL,
        "authorization_endpoint": f"{BASE_URL}/oauth/authorize",
        "token_endpoint": f"{BASE_URL}/oauth/token",
        "revocation_endpoint": f"{BASE_URL}/oauth/revoke",
        "registration_endpoint": f"{BASE_URL}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": SUPPORTED_SCOPES,
    })


@oauth_bp.route("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    return jsonify({
        "resource": MCP_RESOURCE,
        "authorization_servers": [BASE_URL],
        "scopes_supported": SUPPORTED_SCOPES,
    })


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------

@oauth_bp.route("/oauth/register", methods=["POST"])
def oauth_register():
    body = request.get_json(silent=True) or {}
    redirect_uris = body.get("redirect_uris", [])

    if not redirect_uris:
        return jsonify({"error": "invalid_client_metadata", "error_description": "redirect_uris required"}), 400

    for uri in redirect_uris:
        if not _redirect_uri_supported(uri):
            return jsonify({
                "error": "invalid_redirect_uri",
                "error_description": "Redirect URI must be localhost or an OpenAI/ChatGPT callback",
            }), 400

    client_id = secrets.token_urlsafe(16)
    now = datetime.now(timezone.utc).isoformat()

    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO oauth_clients
               (client_id, redirect_uris, client_name, created_at)
               VALUES (?, ?, ?, ?)""",
            (client_id, json.dumps(redirect_uris), body.get("client_name"), now),
        )

    return jsonify({
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }), 201


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------

def _configured_gpt_client() -> dict | None:
    client_id = os.environ.get("GPT_ACTIONS_CLIENT_ID", "")
    client_secret = os.environ.get("GPT_ACTIONS_CLIENT_SECRET", "")
    if not client_id:
        return None
    return {
        "client_id": client_id,
        "redirect_uris": json.dumps([
            "https://chatgpt.com/aip/*/oauth/callback",
            "https://chat.openai.com/aip/*/oauth/callback",
        ]),
        "client_secret_hash": hash_secret(client_secret) if client_secret else None,
        "client_name": "ChatGPT",
    }


def _get_client(client_id: str) -> dict | None:
    configured = _configured_gpt_client()
    if configured and configured["client_id"] == client_id:
        return configured
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
    return dict(row) if row else None


def _redirect_match(pattern: str, redirect_uri: str) -> bool:
    if "*" not in pattern:
        return redirect_uri == pattern
    prefix, suffix = pattern.split("*", 1)
    return redirect_uri.startswith(prefix) and redirect_uri.endswith(suffix)


def _redirect_uri_allowed(client: dict, redirect_uri: str) -> bool:
    allowed = json.loads(client["redirect_uris"])
    return any(_redirect_match(uri, redirect_uri) for uri in allowed)


def _redirect_uri_supported(uri: str) -> bool:
    parsed = urlparse(uri)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}:
        return True
    if parsed.scheme == "https" and (
        host == "chat.openai.com"
        or host == "chatgpt.com"
        or host.endswith(".chat.openai.com")
        or host.endswith(".chatgpt.com")
        or host.endswith(".openai.com")
    ):
        return True
    return False


def _client_display_name(client: dict, redirect_uri: str) -> str:
    if client.get("client_name"):
        return client["client_name"]
    parsed = urlparse(redirect_uri)
    host = (parsed.hostname or "").lower()
    if "openai.com" in host or "chatgpt.com" in host:
        return "ChatGPT"
    return "Claude Code"


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
def oauth_authorize():
    client_id = request.values.get("client_id", "")
    redirect_uri = request.values.get("redirect_uri", "")
    state = request.values.get("state", "")
    code_challenge = request.values.get("code_challenge", "")
    code_challenge_method = request.values.get("code_challenge_method", "S256")
    requested_scope = request.values.get("scope", default_scope())
    requested_resource = request.values.get("resource", GPT_ACTIONS_RESOURCE)

    client = _get_client(client_id)
    if not client or not _redirect_uri_allowed(client, redirect_uri):
        return "Invalid client_id or redirect_uri.", 400

    if code_challenge and code_challenge_method != "S256":
        return _deny_redirect(redirect_uri, state, "invalid_request", "Only S256 PKCE is supported")

    if not code_challenge and not client.get("client_secret_hash"):
        return _deny_redirect(redirect_uri, state, "invalid_request", "code_challenge required")

    if not code_challenge:
        code_challenge_method = "none"

    scope = validate_scope(requested_scope)
    if scope is None:
        return _deny_redirect(redirect_uri, state, "invalid_scope", "Unsupported scope requested")

    resource = validate_resource(requested_resource, default=GPT_ACTIONS_RESOURCE)
    if resource is None:
        return _deny_redirect(redirect_uri, state, "invalid_target", "Unsupported resource requested")

    client_name = _client_display_name(client, redirect_uri)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "login":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            user = users.authenticate(username, password)
            if not user or not users.is_approved(user["id"]):
                return _render_authorize(
                    False, client_name, client_id, redirect_uri, state, code_challenge,
                    code_challenge_method, scope, resource,
                    error="Invalid credentials or account not approved.",
                )
            session["oauth_login_user_id"] = user["id"]

        elif action == "deny":
            return _deny_redirect(redirect_uri, state, "access_denied", "User denied access")

        elif action == "approve":
            user_id = session.get("oauth_login_user_id") or session.get("user_id")
            if not user_id:
                return _render_authorize(
                    False, client_name, client_id, redirect_uri, state, code_challenge,
                    code_challenge_method, scope, resource,
                    error="Session expired. Please log in again.",
                )
            return _issue_code(user_id, client_id, redirect_uri, state, code_challenge, code_challenge_method, scope, resource)

    user_id = session.get("oauth_login_user_id") or session.get("user_id")
    if user_id:
        user = users.get_by_id(user_id)
        return _render_authorize(
            True, client_name, client_id, redirect_uri, state, code_challenge,
            code_challenge_method, scope, resource,
            username=user["username"] if user else "Unknown",
        )
    return _render_authorize(
        False, client_name, client_id, redirect_uri, state, code_challenge,
        code_challenge_method, scope, resource,
    )


def _render_authorize(logged_in, client_name, client_id, redirect_uri, state, code_challenge,
                      code_challenge_method, scope, resource, username=None, error=None):
    return render_template(
        "oauth_authorize.html",
        logged_in=logged_in,
        username=username,
        error=error,
        client_name=client_name,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        resource=resource,
    )


def _issue_code(user_id, client_id, redirect_uri, state, code_challenge, code_challenge_method, scope, resource):
    code = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=_CODE_TTL_MINUTES)).isoformat()

    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO oauth_codes
               (code, client_id, user_id, redirect_uri, pkce_challenge, pkce_method,
                scope, resource, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, client_id, user_id, redirect_uri, code_challenge, code_challenge_method,
             scope, resource, now.isoformat(), expires_at),
        )

    session.pop("oauth_login_user_id", None)

    params = {"code": code, "state": state} if state else {"code": code}
    return redirect(f"{redirect_uri}?{urlencode(params)}")


def _deny_redirect(redirect_uri, state, error, description):
    params = {"error": error, "error_description": description}
    if state:
        params["state"] = state
    return redirect(f"{redirect_uri}?{urlencode(params)}")


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------

def _verify_pkce(verifier: str, challenge: str) -> bool:
    if not challenge:
        return True
    digest = hash_secret(verifier)
    raw_digest = bytes.fromhex(digest)
    computed = base64.urlsafe_b64encode(raw_digest).rstrip(b"=").decode()
    return computed == challenge


def _verify_client_secret(client: dict, supplied_secret: str) -> bool:
    expected = client.get("client_secret_hash")
    if not expected:
        return True
    return bool(supplied_secret) and secrets.compare_digest(expected, hash_secret(supplied_secret))


@oauth_bp.route("/oauth/token", methods=["POST"])
def oauth_token():
    grant_type = request.form.get("grant_type") or (request.get_json(silent=True) or {}).get("grant_type")
    if grant_type != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    data = request.form.to_dict()
    if not data:
        data = request.get_json(silent=True) or {}

    code = data.get("code", "")
    redirect_uri = data.get("redirect_uri", "")
    code_verifier = data.get("code_verifier", "")
    client_id = data.get("client_id", "")
    client_secret = data.get("client_secret", "")
    resource = validate_resource(data.get("resource", ""), default=GPT_ACTIONS_RESOURCE)
    if resource is None:
        return jsonify({"error": "invalid_target", "error_description": "Unsupported resource requested"}), 400

    client = _get_client(client_id)
    if not client or not _verify_client_secret(client, client_secret):
        return jsonify({"error": "invalid_client"}), 401

    now = datetime.now(timezone.utc).isoformat()

    with db.get_db() as conn:
        row = conn.execute(
            """SELECT * FROM oauth_codes
               WHERE code=? AND used=0 AND expires_at > ?""",
            (code, now),
        ).fetchone()

        if not row:
            return jsonify({"error": "invalid_grant", "error_description": "Code not found or expired"}), 400

        row = dict(row)
        if row["client_id"] != client_id:
            return jsonify({"error": "invalid_grant", "error_description": "client_id mismatch"}), 400
        if row["redirect_uri"] != redirect_uri:
            return jsonify({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}), 400
        if (row["resource"] or resource) != resource:
            return jsonify({"error": "invalid_grant", "error_description": "resource mismatch"}), 400
        if not _verify_pkce(code_verifier, row["pkce_challenge"]):
            return jsonify({"error": "invalid_grant", "error_description": "PKCE verification failed"}), 400

        conn.execute("UPDATE oauth_codes SET used=1 WHERE code=?", (code,))

        token = secrets.token_urlsafe(48)
        token_hash = hash_secret(token)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=_TOKEN_TTL_DAYS)).isoformat()
        conn.execute(
            """INSERT INTO oauth_tokens
               (token, client_id, user_id, scope, resource, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token_hash, client_id, row["user_id"], row["scope"] or default_scope(), resource, now, expires_at),
        )

    return jsonify({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": _TOKEN_TTL_DAYS * 86400,
        "scope": row["scope"] or default_scope(),
    })


# ---------------------------------------------------------------------------
# Revocation endpoint (RFC 7009)
# ---------------------------------------------------------------------------

@oauth_bp.route("/oauth/revoke", methods=["POST"])
def oauth_revoke():
    token = request.form.get("token") or (request.get_json(silent=True) or {}).get("token", "")
    if token:
        token_hash = hash_secret(token)
        now = datetime.now(timezone.utc).isoformat()
        with db.get_db() as conn:
            conn.execute(
                "UPDATE oauth_tokens SET revoked_at=? WHERE token=? AND revoked_at IS NULL",
                (now, token_hash),
            )
    return "", 200
