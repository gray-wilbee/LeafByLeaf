from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

import db


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def init_db():
    migrations = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            api_key       TEXT UNIQUE,
            created_at    TEXT NOT NULL
        );
        """,
        "",
    ]
    db.run_migrations(db.APP_DB_PATH, migrations)

    with db.get_db() as conn:
        rows = conn.execute("SELECT id, api_key FROM users WHERE api_key IS NOT NULL").fetchall()
        for row in rows:
            if len(row["api_key"]) == 48:
                conn.execute("UPDATE users SET api_key=? WHERE id=?", (_hash_key(row["api_key"]), row["id"]))

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        for col, definition in [
            ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
            ("approved_at", "TEXT"),
            ("approved_by", "INTEGER"),
            ("disabled_at", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")

        conn.execute(
            "UPDATE users SET is_admin=1, approved_at=COALESCE(approved_at, ?) WHERE id=1",
            (datetime.now(timezone.utc).isoformat(),),
        )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                token_hash  TEXT NOT NULL UNIQUE,
                expires_at  TEXT NOT NULL,
                used_at     TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_events_user ON login_events(user_id, created_at);

            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id          TEXT PRIMARY KEY,
                redirect_uris      TEXT NOT NULL,
                client_secret_hash TEXT,
                client_name        TEXT,
                created_at         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_codes (
                code           TEXT PRIMARY KEY,
                client_id      TEXT NOT NULL,
                user_id        INTEGER NOT NULL REFERENCES users(id),
                redirect_uri   TEXT NOT NULL,
                pkce_challenge TEXT NOT NULL,
                pkce_method    TEXT NOT NULL DEFAULT 'S256',
                scope          TEXT,
                resource       TEXT,
                created_at     TEXT NOT NULL,
                expires_at     TEXT NOT NULL,
                used           INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token      TEXT PRIMARY KEY,
                client_id  TEXT NOT NULL,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                scope      TEXT,
                resource   TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user ON oauth_tokens(user_id);
            """
        )

        oauth_client_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_clients)").fetchall()}
        for col, definition in [
            ("client_secret_hash", "TEXT"),
            ("client_name", "TEXT"),
        ]:
            if col not in oauth_client_cols:
                conn.execute(f"ALTER TABLE oauth_clients ADD COLUMN {col} {definition}")

        oauth_code_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_codes)").fetchall()}
        for col, definition in [
            ("scope", "TEXT"),
            ("resource", "TEXT"),
        ]:
            if col not in oauth_code_cols:
                conn.execute(f"ALTER TABLE oauth_codes ADD COLUMN {col} {definition}")

        oauth_token_cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)").fetchall()}
        for col, definition in [
            ("scope", "TEXT"),
            ("resource", "TEXT"),
        ]:
            if col not in oauth_token_cols:
                conn.execute(f"ALTER TABLE oauth_tokens ADD COLUMN {col} {definition}")


def create_user(username: str, password: str) -> int | None:
    now = datetime.now(timezone.utc).isoformat()
    pw_hash = generate_password_hash(password)
    api_key_raw = secrets.token_hex(24)
    api_key_hashed = _hash_key(api_key_raw)
    try:
        with db.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, api_key, created_at) VALUES (?, ?, ?, ?)",
                (username, pw_hash, api_key_hashed, now),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def authenticate(username: str, password: str) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and not row["disabled_at"] and check_password_hash(row["password_hash"], password):
        return dict(row)
    return None


def get_by_api_key(api_key: str) -> dict | None:
    hashed = _hash_key(api_key)
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE api_key=? AND disabled_at IS NULL", (hashed,)
        ).fetchone()
    return dict(row) if row else None


def has_api_key(user_id: int) -> bool:
    with db.get_db() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE id=? AND api_key IS NOT NULL", (user_id,)).fetchone()
    return row is not None


def get_by_id(user_id: int) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_api_key(user_id: int) -> str | None:
    return None


def regenerate_api_key(user_id: int) -> str:
    new_key_raw = secrets.token_hex(24)
    with db.get_db() as conn:
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (_hash_key(new_key_raw), user_id))
    return new_key_raw


def is_admin(user_id: int) -> bool:
    with db.get_db() as conn:
        row = conn.execute("SELECT is_admin FROM users WHERE id=? AND disabled_at IS NULL", (user_id,)).fetchone()
    return bool(row and row["is_admin"])


def is_approved(user_id: int) -> bool:
    with db.get_db() as conn:
        row = conn.execute("SELECT approved_at, disabled_at FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["approved_at"] and not row["disabled_at"])


def approve_user(admin_user_id: int, target_user_id: int) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        cur = conn.execute(
            "UPDATE users SET approved_at=?, approved_by=? WHERE id=? AND approved_at IS NULL",
            (now, admin_user_id, target_user_id),
        )
    return cur.rowcount > 0


def list_all_users() -> list[dict]:
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, username, is_admin, api_key, created_at, approved_at, approved_by, disabled_at
            FROM users ORDER BY id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def create_reset_token(user_id: int, expires_hours: int = 24) -> str:
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_key(raw)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=expires_hours)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token_hash, expires_at.isoformat(), now.isoformat()),
        )
    return raw


def validate_reset_token(token: str) -> int | None:
    token_hash = _hash_key(token)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT user_id FROM password_reset_tokens
            WHERE token_hash=? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return row["user_id"] if row else None


def consume_reset_token(token: str) -> int | None:
    token_hash = _hash_key(token)
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT id, user_id FROM password_reset_tokens
            WHERE token_hash=? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE password_reset_tokens SET used_at=? WHERE id=?", (now, row["id"]))
        return row["user_id"]


def reset_password(user_id: int, new_password: str) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), user_id))


def record_login(user_id: int) -> None:
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO login_events (user_id, created_at) VALUES (?, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()),
        )


def login_count(user_id: int, start: str | None = None, end: str | None = None) -> int:
    where = ["user_id=?"]
    params: list = [user_id]
    if start:
        where.append("created_at >= ?")
        params.append(start)
    if end:
        where.append("created_at <= ?")
        params.append(end)
    with db.get_db() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM login_events WHERE {' AND '.join(where)}", params).fetchone()
    return int(row["cnt"] or 0)
