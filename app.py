from __future__ import annotations

import difflib
import secrets
import json
import logging
import os
import re
import tempfile
import threading
import time
import sys
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Chicago"
TIMEZONE_OPTIONS = [
    {"value": "America/Los_Angeles", "label": "Pacific"},
    {"value": "America/Denver", "label": "Mountain"},
    {"value": "America/Chicago", "label": "Central"},
    {"value": "America/New_York", "label": "Eastern"},
]

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)

import agent_tools
import ai_models
import decisions as decisions_db
import guided_playbooks
import journal
import logs as logs_db
import tasks as tasks_db
import topics
import trackers as trackers_db
import transcribe
import users
import utils
import llm_service

try:
    import bleach
    import markdown as md_lib
except ImportError:  # pragma: no cover - dependency is pinned for production
    bleach = None
    md_lib = None

app = Flask(__name__)
logger = logging.getLogger(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_CONTENT_LENGTH", 50 * 1024 * 1024)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") != "0",
)
if os.environ.get("FLASK_ENV") == "production" and app.secret_key.startswith(("dev-only", "change-me")):
    raise RuntimeError("SECRET_KEY must be set to a strong non-placeholder value in production")

from mcp_blueprint import mcp_bp
from oauth_blueprint import oauth_bp
from gpt_actions_blueprint import gpt_actions_bp
app.register_blueprint(mcp_bp)
app.register_blueprint(oauth_bp)
app.register_blueprint(gpt_actions_bp)

users.init_db()
topics.init_db()
tasks_db.init_db()
logs_db.init_db()
journal.init_entries_db()
guided_playbooks.init_db()
decisions_db.init_db()
trackers_db.init_db()


@app.context_processor
def inject_failed_count():
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    if logged_in():
        return {
            "failed_log_count": logs_db.failed_count(uid()),
            "suggested_count": tasks_db.count_suggested_tasks(uid()),
            "csrf_token": csrf_token,
        }
    return {"failed_log_count": 0, "suggested_count": 0, "csrf_token": csrf_token}

UPLOAD_API_KEY = os.environ.get("UPLOAD_API_KEY", "")
_RATE_LIMITS: dict[tuple[str, str], list[float]] = {}


def _rate_limit_key() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",", 1)[0].strip()


def _check_rate_limit(scope: str, limit: int, window_seconds: int):
    now = time.time()
    key = (scope, _rate_limit_key())
    hits = [ts for ts in _RATE_LIMITS.get(key, []) if now - ts < window_seconds]
    if len(hits) >= limit:
        return jsonify({"error": "rate limit exceeded"}), 429
    hits.append(now)
    _RATE_LIMITS[key] = hits
    return None


@app.before_request
def enforce_csrf_for_session_api():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.endpoint in (
        "upload", "status", "mcp.mcp_endpoint", "oauth.oauth_authorize",
        "oauth.oauth_token", "oauth.oauth_revoke", "oauth.oauth_register",
        "gpt_actions.call_tool", "gpt_actions.batch_call",
    ):
        return None
    if not logged_in():
        return None
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return jsonify({"error": "csrf token missing or invalid"}), 403
    return None


@app.before_request
def enforce_basic_rate_limits():
    if request.endpoint in ("login", "register", "reset_password") and request.method == "POST":
        return _check_rate_limit(request.endpoint, 10, 15 * 60)
    if request.endpoint in ("api_guided_journal_question", "api_guided_journal_transcribe"):
        return _check_rate_limit(request.endpoint, 30, 10 * 60)
    if request.endpoint in ("oauth.oauth_token",):
        return _check_rate_limit(request.endpoint, 20, 10 * 60)
    return None


# ---------------------------------------------------------------------------
# PWA
# ---------------------------------------------------------------------------

@app.route('/sw.js')
def service_worker():
    resp = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route("/privacy")
@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy.html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def logged_in() -> bool:
    return session.get("user_id") is not None


def uid() -> int:
    return session["user_id"]


def decisions_enabled(user_id: int | None = None) -> bool:
    if user_id is None:
        user_id = session.get("user_id")
    return bool(user_id and (int(user_id) == 1 or users.is_admin(user_id)))


def user_tz(user_id: int | None = None) -> ZoneInfo:
    if user_id is None:
        user_id = session.get("user_id")
    tz_name = topics.get_setting(user_id, "timezone", DEFAULT_TZ) if user_id else DEFAULT_TZ
    if tz_name not in {t["value"] for t in TIMEZONE_OPTIONS}:
        tz_name = DEFAULT_TZ
    return ZoneInfo(tz_name)


def format_entry_datetime(date_str: str, time_str: str) -> str:
    try:
        dt = datetime.strptime(f"{date_str} {time_str[:8]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return f"{date_str} {time_str}"
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.strftime('%I:%M %p').lstrip('0')}"


def render_safe_markdown(text: str) -> str:
    if md_lib is None:
        import html
        return html.escape(text or "").replace("\n", "<br>\n")
    raw_html = md_lib.markdown(text or "", extensions=["nl2br", "tables"])
    if bleach is None:
        return raw_html
    allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS) | {
        "p", "br", "hr", "pre", "span", "h1", "h2", "h3",
        "table", "thead", "tbody", "tr", "th", "td",
    }
    allowed_attrs = {
        **bleach.sanitizer.ALLOWED_ATTRIBUTES,
        "a": ["href", "title"],
        "span": ["class"],
        "th": ["align"],
        "td": ["align"],
    }
    return bleach.clean(raw_html, tags=allowed_tags, attributes=allowed_attrs, protocols=["http", "https", "mailto"], strip=True)


def _name_entry_async(user_id: int, entry_id: str, content: str) -> None:
    try:
        title = llm_service.ai_name_entry(content, user_id=user_id)
    except Exception:
        logger.exception("Entry title generation failed")
        title = journal.fallback_entry_title(content)
    journal.update_entry_title(user_id, entry_id, title)


def require_admin_json():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not users.is_admin(uid()):
        return jsonify({"error": "forbidden"}), 403
    return None


def _json_validation_error(exc):
    return jsonify({"error": str(exc)}), 400


def _run_entry_extraction(user_id: int, entry_id: str, date_str: str, content: str) -> None:
    topic_items = llm_service.extract_topics(user_id, entry_id, date_str, content)
    task_items = llm_service.extract_tasks(user_id, entry_id, date_str, content)
    decision_items = []
    if decisions_enabled(user_id):
        decision_items = llm_service.extract_decision_log_items(user_id, entry_id, date_str, content)
    all_items = topic_items + task_items + decision_items
    if all_items:
        try:
            summary = llm_service.generate_extraction_summary(content, all_items, user_id=user_id)
        except Exception:
            logger.exception("Extraction summary failed")
            summary = None
        topics.store_extraction_run(user_id, entry_id, summary, all_items)


def _saved_filter_key(scope: str) -> str:
    if scope not in ("tasks", "journal"):
        raise ValueError("invalid filter scope")
    return f"saved_filters_{scope}"


def _load_saved_filters(user_id: int, scope: str) -> list[dict]:
    raw = topics.get_setting(user_id, _saved_filter_key(scope), "[]")
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _save_saved_filters(user_id: int, scope: str, filters: list[dict]) -> None:
    topics.set_setting(user_id, _saved_filter_key(scope), json.dumps(filters[:30]))


def _topic_tree_tags(all_tags: list[dict]) -> list[dict]:
    topic_rows = [dict(t) for t in all_tags if t.get("kind") == "topic"]
    by_parent: dict[int | None, list[dict]] = {}
    for tag in topic_rows:
        by_parent.setdefault(tag.get("parent_tag_id"), []).append(tag)
    for rows in by_parent.values():
        rows.sort(key=lambda t: (t.get("name") or "").lower())

    result: list[dict] = []

    def walk(parent_id: int | None, depth: int) -> None:
        for tag in by_parent.get(parent_id, []):
            item = dict(tag)
            item["depth"] = depth
            item["has_children"] = bool(by_parent.get(tag["id"]))
            result.append(item)
            walk(tag["id"], depth + 1)

    walk(None, 0)
    return result


def require_api_key():
    key = request.headers.get("X-Api-Key")
    if not key:
        return jsonify({"error": "forbidden"}), 403
    user = users.get_by_api_key(key)
    if not user:
        if UPLOAD_API_KEY and key == UPLOAD_API_KEY:
            return None
        return jsonify({"error": "forbidden"}), 403
    return None


def api_user_from_request() -> dict | None:
    key = request.headers.get("X-Api-Key")
    return users.get_by_api_key(key) if key else None


def _process_job(user_id: int, job_id: str, audio_path: str, date_str: str, time_str: str):
    """Background thread: transcribe → format → append → extract."""
    try:
        raw_text = transcribe.transcribe(audio_path, user_id=user_id)
        formatted = llm_service.format_transcript(raw_text, user_id=user_id)
        entry_id = journal.append_entry(user_id, date_str, time_str, formatted)
        word_count = len(formatted.split())

        logs_db.log_update(job_id, "done", date=date_str, time=time_str,
                           words=word_count, entry_id=entry_id)

        _name_entry_async(user_id, entry_id, formatted)
        _run_entry_extraction(user_id, entry_id, date_str, formatted)
    except Exception as e:
        logger.exception("Background job %s failed", job_id)
        logs_db.log_update(job_id, "error", error=str(e))
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


# ---------------------------------------------------------------------------
# Upload API
# ---------------------------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload():
    err = require_api_key()
    if err:
        return err

    if "audio" not in request.files and not request.data:
        return jsonify({"error": "no audio file"}), 400

    api_user = api_user_from_request()
    upload_user_id = api_user["id"] if api_user else 1

    job_id = str(uuid.uuid4())
    logs_db.log_received(upload_user_id, job_id)

    tmp_path = f"/tmp/vj-{uuid.uuid4()}.m4a"
    try:
        if "audio" in request.files:
            request.files["audio"].save(tmp_path)
        else:
            with open(tmp_path, "wb") as f:
                f.write(request.data)
    except Exception as e:
        logs_db.log_update(job_id, "error", error=f"file save failed: {e}")
        return jsonify({"error": "failed to save audio"}), 500

    now = datetime.now(user_tz(upload_user_id))
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    threading.Thread(
        target=_process_job,
        args=(upload_user_id, job_id, tmp_path, date_str, time_str),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id}), 202


@app.route("/status/<job_id>")
def status(job_id: str):
    err = require_api_key()
    if err:
        return err
    job = logs_db.get_log_by_job_id(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    api_user = api_user_from_request()
    if api_user and job.get("user_id") != api_user["id"]:
        return jsonify({"error": "not found"}), 404
    if not api_user and job.get("user_id") != 1:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        user = users.authenticate(username, pw)
        if user:
            if not users.is_approved(user["id"]):
                error = "Your account is pending approval."
            else:
                session.permanent = True
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["is_admin"] = users.is_admin(user["id"])
                users.record_login(user["id"])
                return redirect(url_for("index"))
        else:
            error = "Invalid credentials."
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if not username or not pw:
            error = "Username and password are required."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(pw) < 6:
            error = "Password must be at least 6 characters."
        elif pw != pw2:
            error = "Passwords do not match."
        else:
            user_id = users.create_user(username, pw)
            if user_id is None:
                error = "Username is already taken."
            else:
                return render_template("register.html", success=True)
    return render_template("register.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if not logged_in():
        return redirect(url_for("login"))
    today = datetime.now(user_tz()).strftime("%Y-%m-%d")
    active_tag_ids = [int(t) for t in request.args.getlist("tag") if t.isdigit()]
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    raw_entries = journal.list_journal_stream(
        uid(),
        tag_ids=active_tag_ids or None,
        date_from=date_from,
        date_to=date_to,
    )
    entry_ids = [e["id"] for e in raw_entries]
    tags_by_entry = topics.get_tags_for_entries(entry_ids)
    entries = [
        {
            "id": e["id"],
            "source": e.get("source", "journal"),
            "source_label": "Chat" if e.get("source", "journal").startswith("chat_") else "Entry",
            "source_icon": "chat" if e.get("source", "journal").startswith("chat_") else "journal",
            "date": e["date"],
            "time": e["time"],
            "display_datetime": format_entry_datetime(e["date"], e["time"]),
            "title": e.get("title") or journal.fallback_entry_title(e["content"]),
            "preview": (e["content"][:160] + "…") if len(e["content"]) > 160 else e["content"],
            "tags": tags_by_entry.get(e["id"], []),
            "is_today": e["date"] == today,
        }
        for e in raw_entries
    ]
    all_tags = topics.list_all_tags(uid())
    return render_template(
        "index.html",
        entries=entries,
        today=today,
        all_tags=all_tags,
        topic_tree_tags=_topic_tree_tags(all_tags),
        active_tag_ids=active_tag_ids,
        date_from=date_from or "",
        date_to=date_to or "",
        guided_playbooks=guided_playbooks.list_playbooks(uid()),
    )


@app.route("/entry/<entry_id>")
def entry_view(entry_id: str):
    if not logged_in():
        return redirect(url_for("login"))
    entry = journal.get_input_by_id(uid(), entry_id)
    if entry is None:
        return render_template("404.html", entry_id=entry_id), 404
    html_content = render_safe_markdown(entry["content"])
    entry_tags = topics.get_tags_for_entry(entry_id)
    highlights = topics.get_highlights_for_entry(entry_id)
    entry_tasks = tasks_db.get_tasks_for_input(entry_id)
    extraction = topics.get_extraction_for_entry(uid(), entry_id)
    if extraction:
        tag_by_id = {t["id"]: t for t in topics.list_all_tags(uid())}
        task_by_id = {t["id"]: t for t in entry_tasks}
        for item in extraction.get("items", []):
            item["url"] = None
            item["color"] = None
            item["emoji"] = None
            if item["item_type"] in ("topic", "entity"):
                tag = tag_by_id.get(int(item["item_id"])) if str(item["item_id"]).isdigit() else None
                if tag:
                    item["color"] = tag.get("color")
                    item["url"] = url_for("entity_detail", entity_id=tag["id"], from_entry=entry_id) if tag["kind"] == "entity" else url_for("topic_detail", topic_id=tag["id"], from_entry=entry_id)
            elif item["item_type"] in ("task", "task_duplicate"):
                task = task_by_id.get(item["item_id"]) or tasks_db.get_task(uid(), item["item_id"])
                if task:
                    item["emoji"] = task.get("emoji")
                    item["url"] = url_for("tasks_list", task=item["item_id"], from_entry=entry_id)
    return render_template(
        "entry.html",
        entry_id=entry_id,
        date=entry["date"],
        time_str=entry["time"],
        display_datetime=format_entry_datetime(entry["date"], entry["time"]),
        entry_title=entry.get("title") or journal.fallback_entry_title(entry["content"]),
        raw_content=entry["content"],
        source=entry.get("source", "journal"),
        editable=entry.get("source", "journal") == "journal",
        html_content=html_content,
        entry_tags=entry_tags,
        highlights=highlights,
        entry_tasks=entry_tasks,
        extraction=extraction,
    )


@app.route("/entry/<entry_id>/edit", methods=["POST"])
def entry_edit(entry_id: str):
    if not logged_in():
        return redirect(url_for("login"))
    entry = journal.get_entry_by_id(uid(), entry_id)
    if not entry:
        return redirect(url_for("index"))
    old_date, old_time = entry["date"], entry["time"]
    new_date = request.form.get("date", old_date).strip()
    new_time = request.form.get("time", old_time).strip()
    new_content = request.form.get("content", "").strip()
    journal.update_entry(uid(), old_date, old_time, new_date, new_time, new_content)
    if new_date != old_date and not journal.has_entries(uid(), old_date):
        topics.move_entry_date(old_date, new_date)
    return redirect(url_for("entry_view", entry_id=entry_id))


# ---------------------------------------------------------------------------
# Logs & Settings
# ---------------------------------------------------------------------------

@app.route("/logs")
def logs_view():
    if not logged_in():
        return redirect(url_for("login"))
    logs_db.mark_all_viewed(uid())
    return render_template("logs.html", entries=logs_db.list_logs(uid()))


SETTINGS_META = [
    {"key": "timezone", "label": "Timezone", "type": "select", "options": TIMEZONE_OPTIONS, "help": "Controls timestamps for entries and task due dates."},
    {"key": "user_profile", "label": "About Me", "type": "textarea", "rows": 10},
    {"key": "sort_instructions", "label": "Custom Sorting Instructions", "type": "textarea", "rows": 5},
]

@app.route("/settings")
def settings_view():
    if not logged_in():
        return redirect(url_for("login"))
    current = topics.get_all_settings(uid())
    return render_template(
        "settings.html",
        settings_meta=SETTINGS_META,
        current=current,
        api_key=users.get_api_key(uid()),
        api_key_configured=users.has_api_key(uid()),
    )


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    for key, value in data.items():
        topics.set_setting(uid(), key, str(value))
    return jsonify({"ok": True})


@app.route("/api/settings/regenerate-key", methods=["POST"])
def api_regenerate_key():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    new_key = users.regenerate_api_key(uid())
    return jsonify({"ok": True, "api_key": new_key})


# ---------------------------------------------------------------------------
# Tasks page
# ---------------------------------------------------------------------------

@app.route("/tasks/today")
def tasks_today():
    return redirect(url_for("tasks_list", tab="today"))


@app.route("/tasks/review")
def tasks_review():
    return redirect(url_for("tasks_list", tab="review"))


@app.route("/tasks")
def tasks_list():
    if not logged_in():
        return redirect(url_for("login"))

    # ── All Tasks data ──────────────────────────────────────
    show_done = request.args.get("show_done") == "1"
    show_cancelled = request.args.get("show_cancelled") == "1"
    sort_by = request.args.get("sort", "default")
    active_tag_ids = [int(t) for t in request.args.getlist("tag") if t.isdigit()]
    tag_id = request.args.get("tag")
    if tag_id and tag_id.isdigit() and int(tag_id) not in active_tag_ids:
        active_tag_ids.append(int(tag_id))

    all_tasks = tasks_db.list_tasks_sorted(
        uid(),
        show_done=True,
        show_cancelled=True,
        sort_by=sort_by,
        tag_ids=active_tag_ids or None,
    )
    active_tasks = [t for t in all_tasks if t["status"] not in ("done", "cancelled")]
    done_tasks = [t for t in all_tasks if t["status"] == "done"] if show_done else []
    cancelled_tasks = [t for t in all_tasks if t["status"] == "cancelled"] if show_cancelled else []
    all_tags = topics.list_all_tags_with_task_counts(uid())

    # ── Today data ──────────────────────────────────────────
    today = datetime.now(user_tz()).strftime("%Y-%m-%d")
    week_end = (datetime.now(user_tz()) + timedelta(days=7)).strftime("%Y-%m-%d")
    today_sections = tasks_db.list_today_tasks(uid(), today=today, week_end=week_end, tag_ids=active_tag_ids or None)
    today_has_tasks = any(today_sections[k] for k in ("must", "should", "tiny_wins"))

    # ── Review data ─────────────────────────────────────────
    suggested = tasks_db.list_suggested_tasks(uid())
    entry_id_set = {t["source_entry_id"] for t in suggested if t.get("source_entry_id")}
    entry_titles = {}
    for eid in entry_id_set:
        entry = journal.get_entry_by_id(uid(), eid)
        if entry:
            entry_titles[eid] = entry.get("title") or entry.get("occurred_at", "")[:10]

    # ── Shared tag map ──────────────────────────────────────
    today_task_ids = (
        [t["id"] for t in today_sections["must"]]
        + [t["id"] for t in today_sections["should"]]
        + [t["id"] for t in today_sections["tiny_wins"]]
        + [t["id"] for t in today_sections["rest"]]
    )
    all_task_ids = [t["id"] for t in all_tasks] + [t["id"] for t in suggested] + today_task_ids
    task_tags = tasks_db.get_task_tag_map(uid(), list(dict.fromkeys(all_task_ids)))

    # ── Default tab ─────────────────────────────────────────
    requested_tab = request.args.get("tab", "")
    if requested_tab in ("review", "today", "all"):
        default_tab = requested_tab
    elif suggested:
        default_tab = "review"
    elif today_has_tasks:
        default_tab = "today"
    else:
        default_tab = "all"

    return render_template(
        "tasks.html",
        # shared
        today=today,
        task_tags=task_tags,
        default_tab=default_tab,
        from_entry=request.args.get("from_entry"),
        # review
        suggested=suggested,
        entry_titles=entry_titles,
        # today
        must=today_sections["must"],
        should=today_sections["should"],
        tiny_wins=today_sections["tiny_wins"],
        rest=today_sections["rest"],
        today_has_tasks=today_has_tasks,
        # all tasks
        active_tasks=active_tasks,
        done_tasks=done_tasks,
        cancelled_tasks=cancelled_tasks,
        sort_by=sort_by,
        show_done=show_done,
        show_cancelled=show_cancelled,
        active_tag_ids=active_tag_ids,
        all_tags=all_tags,
        topic_tree_tags=_topic_tree_tags(all_tags),
        root_topics=topics.list_root_topics(uid()),
        all_entities=topics.list_entities(uid()),
    )


# ---------------------------------------------------------------------------
# Topics & entities pages
# ---------------------------------------------------------------------------

def _render_note_entries(entries):
    rendered = []
    for entry in entries:
        item = dict(entry)
        item["content_html"] = render_safe_markdown(item.get("content") or "")
        rendered.append(item)
    return rendered


@app.route("/topics")
def topics_list():
    if not logged_in():
        return redirect(url_for("login"))
    return render_template("topics.html", topics=topics.list_topics(uid()))


@app.route("/topics/<int:topic_id>")
def topic_detail(topic_id):
    if not logged_in():
        return redirect(url_for("login"))
    topic = topics.get_topic(uid(), topic_id)
    if not topic:
        return redirect(url_for("topics_list"))

    topic_dict = dict(topic)
    topic_dict["summary_html"] = render_safe_markdown(topic_dict.get("summary") or "")
    all_entries = topics.list_topic_entries(topic_id, include_archived=True)
    entries = _render_note_entries([e for e in all_entries if not e["is_archived"]])
    archived_entries = _render_note_entries([e for e in all_entries if e["is_archived"]])
    descendants = topics.get_descendants(topic_id)
    excluded_ids = {topic_id} | set(descendants)

    decision_items = []
    decision_tag_map = {}
    if decisions_enabled():
        decision_items = decisions_db.list_for_tags(uid(), [topic_id], include_children=True)
        decision_tag_map = decisions_db.get_decision_tag_map(uid(), [i["id"] for i in decision_items])
    return render_template(
        "topic_detail.html",
        topic=topic_dict,
        entries=entries,
        archived_entries=archived_entries,
        topic_tasks=tasks_db.list_tasks_for_tags(uid(), [topic_id], show_done=True, show_cancelled=True),
        related_tags=topics.get_related_tags(uid(), topic_id),
        chats=topics.list_chats(topic_id),
        other_topics=[t for t in topics.list_topics(uid()) if t["id"] != topic_id],
        parent=topics.get_parent(topic_id),
        children=topics.get_children(topic_id),
        parent_candidates=[t for t in topics.list_topics(uid()) if t["id"] not in excluded_ids],
        palette=topics.PALETTE,
        today=datetime.now(user_tz()).strftime("%Y-%m-%d"),
        from_entry=request.args.get("from_entry"),
        all_tags=topics.list_all_tags(uid()),
        decision_items=decision_items,
        decision_tag_map=decision_tag_map,
    )


@app.route("/entities")
def entities_list():
    if not logged_in():
        return redirect(url_for("login"))
    return render_template("entities.html", entities=topics.list_entities(uid()))


@app.route("/entities/<int:entity_id>")
def entity_detail(entity_id):
    if not logged_in():
        return redirect(url_for("login"))
    entity = topics.get_entity(uid(), entity_id)
    if not entity:
        return redirect(url_for("entities_list"))

    entity_dict = dict(entity)
    entity_dict["summary_html"] = render_safe_markdown(entity_dict.get("summary") or "")
    all_entries = topics.list_topic_entries(entity_id, include_archived=True)
    entries = _render_note_entries([e for e in all_entries if not e["is_archived"]])
    archived_entries = _render_note_entries([e for e in all_entries if e["is_archived"]])

    return render_template(
        "entity_detail.html",
        entity=entity_dict,
        entries=entries,
        archived_entries=archived_entries,
        related_tags=topics.get_related_tags(uid(), entity_id),
        backlinks=topics.get_tag_links(entity_id),
        chats=topics.list_chats(entity_id),
        other_entities=[e for e in topics.list_entities(uid()) if e["id"] != entity_id],
        all_tags=topics.list_all_tags(uid()),
        palette=topics.PALETTE,
        today=datetime.now(user_tz()).strftime("%Y-%m-%d"),
        from_entry=request.args.get("from_entry"),
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@app.route("/chat")
@app.route("/chat/<int:chat_id>", endpoint="chat_page_active")
def chat_page(chat_id=None):
    if not logged_in():
        return redirect(url_for("login"))
    all_chats = topics.list_chats_all(uid())
    active_chat = topics.get_chat(uid(), chat_id) if chat_id else None
    scope_tags = topics.get_scope_tags(chat_id) if active_chat else []
    saved_as_input = topics.chat_has_been_saved(chat_id) if active_chat else False
    return render_template(
        "chat.html",
        all_chats=all_chats,
        active_chat=active_chat,
        scope_tags=scope_tags,
        saved_as_input=saved_as_input,
        chat_agent_instructions=topics.get_setting(uid(), "chat_agent_instructions", "") or llm_service.default_chat_agent_instructions(),
    )


@app.route("/api/filters/<scope>", methods=["GET", "POST"])
def api_saved_filters(scope):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        filters = _load_saved_filters(uid(), scope)
    except ValueError:
        return jsonify({"error": "invalid scope"}), 400
    if request.method == "GET":
        return jsonify({"filters": filters})
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    params = data.get("params") or {}
    if not name:
        return jsonify({"error": "name required"}), 400
    filters = [f for f in filters if f.get("name") != name]
    filters.insert(0, {"name": name, "params": params})
    _save_saved_filters(uid(), scope, filters)
    return jsonify({"ok": True, "filters": filters})


@app.route("/api/filters/<scope>/<path:name>", methods=["DELETE"])
def api_delete_saved_filter(scope, name):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        filters = _load_saved_filters(uid(), scope)
    except ValueError:
        return jsonify({"error": "invalid scope"}), 400
    filters = [f for f in filters if f.get("name") != name]
    _save_saved_filters(uid(), scope, filters)
    return jsonify({"ok": True, "filters": filters})


@app.route("/api/chat/instructions", methods=["GET", "POST"])
def api_chat_instructions():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    default_text = llm_service.default_chat_agent_instructions() if hasattr(llm_service, "default_chat_agent_instructions") else ""
    if request.method == "GET":
        return jsonify({
            "instructions": topics.get_setting(uid(), "chat_agent_instructions", "") or default_text,
            "default": default_text,
        })
    text = (request.get_json(silent=True) or {}).get("instructions")
    if text is None:
        return jsonify({"error": "instructions required"}), 400
    topics.set_setting(uid(), "chat_agent_instructions", str(text).strip())
    return jsonify({"ok": True})


@app.route("/api/chat/<int:chat_id>/title")
def api_chat_title(chat_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    chat = topics.get_chat(uid(), chat_id)
    if not chat:
        return jsonify({"error": "not found"}), 404
    return jsonify({"title": chat["title"]})


@app.route("/api/chats", methods=["GET", "POST"])
def api_chats():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "New Chat").strip()
        scope_ids = data.get("scope_tag_ids") or []
        if scope_ids:
            chat_id = topics.create_chat(uid(), scope_ids[0], title)
            for tid in scope_ids[1:]:
                topics.add_scope_tag(chat_id, tid)
        else:
            chat_id = topics.create_chat_unscoped(uid(), title)
        return jsonify({"chat_id": chat_id})
    return jsonify({"chats": topics.list_chats_all(uid())})


@app.route("/api/chat/<int:chat_id>/messages")
def api_chat_messages(chat_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    all_msgs = topics.get_messages(chat_id)
    display_msgs = _display_chat_messages(all_msgs)
    return jsonify({"messages": display_msgs})


def _display_chat_messages(messages):
    """Return user-facing chat messages with compact tool metadata attached."""
    display_msgs = []

    for msg in messages:
        content_type = msg.get("content_type", "text")

        if content_type == "tool_turn":
            try:
                blocks = json.loads(msg["content"])
            except Exception:
                blocks = []
            tools = []
            text = ""
            for block in blocks:
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tools.append({
                        "id": block.get("id"),
                        "name": block.get("name", "tool"),
                        "input": block.get("input") or {},
                        "summary": "Done",
                        "status": "done",
                    })
            display_msgs.append({
                "role": "assistant",
                "content": text,
                "content_type": "text",
                "tools": tools,
            })
            continue

        if content_type == "tool_results":
            continue

        display_msgs.append(dict(msg))

    return display_msgs


@app.route("/api/chat/<int:chat_id>/send", methods=["POST"])
def api_chat_send(chat_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    user_text = data.get("message", "").strip()
    if not user_text:
        return jsonify({"error": "empty message"}), 400

    history = topics.get_messages(chat_id)
    is_first = len(history) == 0
    topics.add_message(chat_id, "user", user_text)

    system_prompt = llm_service.build_chat_system_prompt(uid(), chat_id)
    messages = []
    for m in history:
        ct = m.get("content_type", "text")
        if ct in ("tool_turn", "tool_results"):
            messages.append({"role": m["role"], "content": json.loads(m["content"])})
        else:
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_text})

    def generate():
        accumulated = ""
        current_msgs = list(messages)
        for _round in range(10):
            round_result = None
            try:
                for event in llm_service.stream_chat_model_round(
                    user_id=uid(),
                    system_prompt=system_prompt,
                    messages=current_msgs,
                    tools_schema=agent_tools.get_tools_schema(uid()),
                ):
                    if event["type"] == "sse":
                        yield event["data"]
                    elif event["type"] == "done":
                        round_result = event
            except Exception as exc:
                logger.exception("Chat model stream failed for chat_id=%s", chat_id)
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429:
                    error_text = (
                        "I made the tool changes, but the model provider hit a rate limit before I could "
                        "finish the written response. Please try again in a minute."
                    )
                else:
                    error_text = "The model provider stopped responding before I could finish. Please try again."
                accumulated += error_text
                yield (
                    "data: "
                    + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": error_text}})
                    + "\n\n"
                ).encode("utf-8")
                break

            if not round_result:
                break

            round_text = round_result.get("round_text") or ""
            tool_uses = round_result.get("tool_uses") or []
            stop_reason = round_result.get("stop_reason")
            if stop_reason in ("max_tokens", "length", "MAX_TOKENS"):
                logger.warning("Chat model hit token limit for chat_id=%s user_id=%s", chat_id, uid())

            if stop_reason != "tool_use" or not tool_uses:
                accumulated += round_text
                break

            assistant_content = []
            if round_text: assistant_content.append({"type": "text", "text": round_text})
            tool_results = []
            for tu in tool_uses:
                try: tool_input = json.loads(tu["input_json_str"])
                except: tool_input = {}
                tool_block = {"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tool_input}
                if tu.get("provider_metadata"):
                    tool_block["provider_metadata"] = tu["provider_metadata"]
                assistant_content.append(tool_block)
                yield f"event: tool_call\ndata: {json.dumps({'name': tu['name'], 'input': tool_input})}\n\n".encode("utf-8")
                res = agent_tools.dispatch(tu["name"], tool_input, chat_id, uid())
                summary = "Done" # Or full logic from before
                yield f"event: tool_result\ndata: {json.dumps({'name': tu['name'], 'summary': summary})}\n\n".encode("utf-8")
                tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(res)})

            current_msgs.append({"role": "assistant", "content": assistant_content})
            current_msgs.append({"role": "user", "content": tool_results})
            topics.add_tool_turn(chat_id, assistant_content, tool_results)

        if accumulated:
            topics.add_message(chat_id, "assistant", accumulated)
            if is_first:
                try:
                    title = llm_service.ai_name_chat(topics.get_messages(chat_id), user_id=uid())
                    if title:
                        title = title[:120]
                        topics.rename_chat(chat_id, title)
                        yield f"event: chat_renamed\ndata: {json.dumps({'title': title})}\n\n".encode("utf-8")
                except Exception:
                    logger.exception("Chat title generation failed")
        yield b"data: [DONE]\n\n"

    return Response(stream_with_context(generate()), content_type="text/event-stream")


@app.route("/api/chat/<int:chat_id>/save-summary", methods=["POST"])
def api_chat_save_summary(chat_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_chat(uid(), chat_id):
        return jsonify({"error": "not found"}), 404
    msgs = [m for m in topics.get_messages(chat_id) if m.get("content_type", "text") == "text"]
    if not msgs:
        return jsonify({"error": "no messages to save"}), 400
    transcript = "\n\n".join(f"**{m['role'].title()}**: {m['content']}" for m in msgs)
    summary = llm_service.ai_summarize_chat(transcript, user_id=uid())
    tz = user_tz()
    now_dt = datetime.now(tz)
    now = journal.local_occurred_at(now_dt, tz.key)
    input_id = journal.create_input(uid(), "chat_summary", summary, occurred_at=now)
    chat = topics.get_chat(uid(), chat_id)
    journal.update_entry_title(uid(), input_id, f"Chat Summary: {chat['title'] if chat else now[:10]}")
    scope_tags = topics.get_scope_tags(chat_id)
    if scope_tags:
        topics.tag_entry(uid(), input_id, [t["id"] for t in scope_tags], tag_source="user_context")
    topics.log_chat_action(chat_id, "saved_summary", "input", input_id)
    threading.Thread(
        target=_run_entry_extraction,
        args=(uid(), input_id, now[:10], summary),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "input_id": input_id})


@app.route("/api/chat/<int:chat_id>/save-transcript", methods=["POST"])
def api_chat_save_transcript(chat_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_chat(uid(), chat_id):
        return jsonify({"error": "not found"}), 404
    messages = topics.get_messages(chat_id)
    if not messages:
        return jsonify({"error": "no messages to save"}), 400
    transcript = "\n\n".join(f"**{m['role'].title()}**: {m['content']}" for m in messages)
    tz = user_tz()
    now_dt = datetime.now(tz)
    now = journal.local_occurred_at(now_dt, tz.key)
    input_id = journal.create_input(uid(), "chat_transcript", transcript, occurred_at=now)
    chat = topics.get_chat(uid(), chat_id)
    journal.update_entry_title(uid(), input_id, f"Chat Transcript: {chat['title'] if chat else now[:10]}")
    scope_tags = topics.get_scope_tags(chat_id)
    if scope_tags:
        topics.tag_entry(uid(), input_id, [t["id"] for t in scope_tags], tag_source="user_context")
    topics.log_chat_action(chat_id, "saved_transcript", "input", input_id)
    threading.Thread(
        target=_run_entry_extraction,
        args=(uid(), input_id, now[:10], transcript),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "input_id": input_id})


@app.route("/api/chat/<int:chat_id>/rename", methods=["POST"])
def api_chat_rename(chat_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_chat(uid(), chat_id):
        return jsonify({"error": "not found"}), 404
    title = ((request.get_json(silent=True) or {}).get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    topics.rename_chat(chat_id, title[:120])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Topics & Entities API
# ---------------------------------------------------------------------------

@app.route("/api/entries", methods=["POST"])
def api_new_entry():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    raw_content = (data.get("content") or "").strip()
    if not raw_content:
        return jsonify({"error": "content required"}), 400
    try:
        formatted = llm_service.format_transcript(raw_content, user_id=uid())
    except Exception:
        logger.exception("Manual entry formatting failed")
        return jsonify({"error": "entry formatting failed"}), 502

    formatted = formatted.strip()
    if not formatted:
        return jsonify({"error": "formatted entry was empty"}), 502

    current_user_id = uid()
    now = datetime.now(user_tz())
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    entry_id = journal.append_entry(current_user_id, date_str, time_str, formatted)
    if title:
        journal.update_entry_title(current_user_id, entry_id, title)
    else:
        threading.Thread(target=_name_entry_async, args=(current_user_id, entry_id, formatted), daemon=True).start()
    logs_db.log_received(current_user_id, entry_id)
    logs_db.log_update(
        entry_id,
        "done",
        date=date_str,
        time=time_str,
        words=len(formatted.split()),
        entry_id=entry_id,
    )
    threading.Thread(
        target=_run_entry_extraction,
        args=(current_user_id, entry_id, date_str, formatted),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "entry_id": entry_id})


@app.route("/api/guided-journal/question", methods=["POST"])
def api_guided_journal_question():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    answers = data.get("answers") if isinstance(data.get("answers"), list) else []
    skipped_questions = data.get("skipped_questions") if isinstance(data.get("skipped_questions"), list) else []
    objective = (data.get("objective") or "").strip()
    playbook = guided_playbooks.get_playbook(uid(), data.get("playbook_id"))

    now = datetime.now(user_tz())
    time_context = (
        f"{now.strftime('%A')}, {now.strftime('%Y-%m-%d')} at "
        f"{now.strftime('%I:%M %p').lstrip('0')} ({user_tz().key})"
    )
    try:
        question = llm_service.guided_journal_question(
            recent_context=journal.get_context(uid(), n_recent=7),
            time_context=time_context,
            objective=objective,
            answers=answers,
            skipped_questions=skipped_questions,
            playbook=playbook,
            user_id=uid(),
        )
    except Exception:
        logger.exception("Guided journal question generation failed")
        return jsonify({"error": "question generation failed"}), 502
    if isinstance(question, dict):
        return jsonify(question)
    return jsonify({"question": question, "question_type": "reflection", "debug_reason": ""})


@app.route("/api/guided-journal/playbooks", methods=["GET", "POST"])
def api_guided_playbooks():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "GET":
        return jsonify({"playbooks": guided_playbooks.list_playbooks(uid())})
    try:
        playbook = guided_playbooks.save_playbook(uid(), request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "playbook": playbook}), 201


@app.route("/api/guided-journal/playbooks/<playbook_id>", methods=["POST", "DELETE"])
def api_guided_playbook_detail(playbook_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        if request.method == "DELETE":
            guided_playbooks.delete_playbook(uid(), playbook_id)
            return jsonify({"ok": True})
        playbook = guided_playbooks.save_playbook(uid(), request.get_json(silent=True) or {}, playbook_id)
        return jsonify({"ok": True, "playbook": playbook})
    except KeyError:
        return jsonify({"error": "not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/guided-journal/playbooks/<playbook_id>/duplicate", methods=["POST"])
def api_guided_playbook_duplicate(playbook_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    try:
        playbook = guided_playbooks.duplicate_playbook(uid(), playbook_id)
    except KeyError:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "playbook": playbook}), 201


@app.route("/api/guided-journal/transcribe", methods=["POST"])
def api_guided_journal_transcribe():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if "audio" not in request.files:
        return jsonify({"error": "audio required"}), 400

    audio = request.files["audio"]
    filename = audio.filename or "guided-journal.webm"
    suffix = os.path.splitext(filename)[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(prefix="vj-guided-", suffix=suffix, delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        audio.save(tmp_path)
        text = transcribe.transcribe(tmp_path, user_id=uid())
    except Exception:
        logger.exception("Guided journal transcription failed")
        return jsonify({"error": "transcription failed"}), 502
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return jsonify({"text": text})


@app.route("/api/topics/create", methods=["POST"])
def api_topic_create():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    description = (data.get("description") or "").strip() or None
    parent_tag_id = data.get("parent_tag_id") or None
    topic_id = topics.create_topic(uid(), name, description, parent_tag_id=parent_tag_id)
    return jsonify({"ok": True, "topic_id": topic_id})


@app.route("/api/topics/<int:topic_id>", methods=["POST"])
def api_topic_update(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_topic(uid(), topic_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    allowed = {k: v for k, v in data.items() if k in ("name", "description", "summary", "color", "parent_tag_id")}
    if allowed:
        topics.update_topic(uid(), topic_id, **allowed)
    return jsonify({"ok": True})


@app.route("/api/topics/<int:topic_id>/refresh-description", methods=["POST"])
def api_topic_refresh_desc(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    t = topics.get_topic(uid(), topic_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    desc = llm_service.ai_refresh_description(t["name"], topics.get_all_entry_content(topic_id) or "(No entries yet.)", user_id=uid())
    topics.update_topic(uid(), topic_id, description=desc)
    return jsonify({"description": desc})


@app.route("/api/topics/<int:topic_id>/refresh-summary", methods=["POST"])
def api_topic_refresh_summ(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    t = topics.get_topic(uid(), topic_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    summ = llm_service.ai_refresh_summary(t["name"], t["description"] or "", topics.get_all_entry_content(topic_id) or "(No entries yet.)", user_id=uid())
    topics.update_topic(uid(), topic_id, summary=summ)
    return jsonify({"summary": summ})


@app.route("/api/topics/<int:topic_id>/compact", methods=["POST"])
def api_topic_compact(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    topic = topics.get_topic(uid(), topic_id)
    if not topic:
        return jsonify({"error": "not found"}), 404
    if not topics.list_topic_entries(topic_id):
        return jsonify({"error": "no entries to compact"}), 400
    compact_content = llm_service.ai_compact(topic["name"], topics.get_all_entry_content(topic_id), user_id=uid())
    today = datetime.now(user_tz()).strftime("%Y-%m-%d")
    new_id = topics.upsert_topic_entry(uid(), topic_id, today, compact_content)
    topics.archive_topic_entries(topic_id, keep_ids=[new_id])
    return jsonify({"ok": True, "new_entry_id": new_id})


@app.route("/api/topics/<int:topic_id>/merge", methods=["POST"])
def api_topic_merge(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    target_id = (request.get_json(silent=True) or {}).get("target_id")
    if not target_id or int(target_id) == topic_id:
        return jsonify({"error": "invalid target"}), 400
    if not topics.get_topic(uid(), topic_id) or not topics.get_topic(uid(), int(target_id)):
        return jsonify({"error": "not found"}), 404
    topics.merge_topics(source_id=topic_id, target_id=int(target_id))
    return jsonify({"ok": True, "redirect": url_for("topic_detail", topic_id=int(target_id))})


@app.route("/api/topics/<int:topic_id>/delete", methods=["POST"])
def api_topic_delete(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    topics.delete_topic(uid(), topic_id)
    return jsonify({"ok": True})


@app.route("/api/tags/<int:tag_id>/links", methods=["POST"])
def api_tag_link(tag_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    tag = topics.get_topic(uid(), tag_id) or topics.get_entity(uid(), tag_id)
    if not tag:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    to_tag_id = data.get("to_tag_id")
    if not to_tag_id:
        return jsonify({"error": "to_tag_id required"}), 400
    if int(to_tag_id) == tag_id:
        return jsonify({"error": "cannot link tag to itself"}), 400
    target = topics.get_topic(uid(), int(to_tag_id)) or topics.get_entity(uid(), int(to_tag_id))
    if not target:
        return jsonify({"error": "target not found"}), 404
    note = (data.get("note") or "").strip() or None
    topics.add_tag_link(uid(), tag_id, int(to_tag_id), note=note, source="user")
    return jsonify({"ok": True, "links": topics.get_tag_links(tag_id)})


@app.route("/api/tags/<int:tag_id>/unlink", methods=["POST"])
def api_tag_unlink(tag_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    tag = topics.get_topic(uid(), tag_id) or topics.get_entity(uid(), tag_id)
    if not tag:
        return jsonify({"error": "not found"}), 404
    to_tag_id = (request.get_json(silent=True) or {}).get("to_tag_id")
    if not to_tag_id:
        return jsonify({"error": "to_tag_id required"}), 400
    topics.remove_tag_link(tag_id, int(to_tag_id))
    return jsonify({"ok": True, "links": topics.get_tag_links(tag_id)})


@app.route("/api/topics/<int:topic_id>/set-parent", methods=["POST"])
def api_topic_set_parent(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_topic(uid(), topic_id):
        return jsonify({"error": "not found"}), 404
    parent_tag_id = (request.get_json(silent=True) or {}).get("parent_tag_id")
    if parent_tag_id is not None:
        parent_tag_id = int(parent_tag_id)
        descendants = topics.get_descendants(topic_id)
        if parent_tag_id == topic_id or parent_tag_id in descendants:
            return jsonify({"error": "circular hierarchy not allowed"}), 400
    topics.update_topic(uid(), topic_id, parent_tag_id=parent_tag_id)
    return jsonify({"ok": True})


@app.route("/api/topics/<int:topic_id>/entries", methods=["POST"])
def api_add_topic_note(topic_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_topic(uid(), topic_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    date_str = data.get("date") or datetime.now(user_tz()).strftime("%Y-%m-%d")
    entry_id = topics.upsert_topic_entry(uid(), topic_id, date_str, content)
    return jsonify({"ok": True, "entry_id": entry_id})


@app.route("/api/entities/create", methods=["POST"])
def api_entity_create():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    description = (data.get("description") or "").strip() or None
    entity_id = topics.create_entity(uid(), name, description)
    return jsonify({"ok": True, "entity_id": entity_id})


@app.route("/api/entities/<int:entity_id>", methods=["POST"])
def api_entity_update(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_entity(uid(), entity_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    allowed = {k: v for k, v in data.items() if k in ("name", "description", "summary", "color", "parent_tag_id")}
    if allowed:
        topics.update_entity(uid(), entity_id, **allowed)
    return jsonify({"ok": True})


@app.route("/api/entities/<int:entity_id>/refresh-description", methods=["POST"])
def api_entity_refresh_desc(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    entity = topics.get_entity(uid(), entity_id)
    if not entity:
        return jsonify({"error": "not found"}), 404
    desc = llm_service.ai_refresh_description(entity["name"], topics.get_all_entry_content(entity_id) or "(No entries yet.)", user_id=uid())
    topics.update_entity(uid(), entity_id, description=desc)
    return jsonify({"description": desc})


@app.route("/api/entities/<int:entity_id>/refresh-summary", methods=["POST"])
def api_entity_refresh_summ(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    entity = topics.get_entity(uid(), entity_id)
    if not entity:
        return jsonify({"error": "not found"}), 404
    summ = llm_service.ai_refresh_summary(entity["name"], entity["description"] or "", topics.get_all_entry_content(entity_id) or "(No entries yet.)", user_id=uid())
    topics.update_entity(uid(), entity_id, summary=summ)
    return jsonify({"summary": summ})


@app.route("/api/entities/<int:entity_id>/compact", methods=["POST"])
def api_entity_compact(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    entity = topics.get_entity(uid(), entity_id)
    if not entity:
        return jsonify({"error": "not found"}), 404
    if not topics.list_topic_entries(entity_id):
        return jsonify({"error": "no entries to compact"}), 400
    compact_content = llm_service.ai_compact(entity["name"], topics.get_all_entry_content(entity_id), user_id=uid())
    today = datetime.now(user_tz()).strftime("%Y-%m-%d")
    new_id = topics.upsert_topic_entry(uid(), entity_id, today, compact_content)
    topics.archive_topic_entries(entity_id, keep_ids=[new_id])
    return jsonify({"ok": True, "new_entry_id": new_id})


@app.route("/api/entities/<int:entity_id>/merge", methods=["POST"])
def api_entity_merge(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    target_id = (request.get_json(silent=True) or {}).get("target_id")
    if not target_id or int(target_id) == entity_id:
        return jsonify({"error": "invalid target"}), 400
    if not topics.get_entity(uid(), entity_id) or not topics.get_entity(uid(), int(target_id)):
        return jsonify({"error": "not found"}), 404
    topics.merge_topics(source_id=entity_id, target_id=int(target_id))
    return jsonify({"ok": True, "redirect": url_for("entity_detail", entity_id=int(target_id))})


@app.route("/api/entities/<int:entity_id>/delete", methods=["POST"])
def api_entity_delete(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    topics.delete_entity(uid(), entity_id)
    return jsonify({"ok": True})


@app.route("/api/entities/<int:entity_id>/entries", methods=["POST"])
def api_add_entity_note(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_entity(uid(), entity_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    date_str = data.get("date") or datetime.now(user_tz()).strftime("%Y-%m-%d")
    entry_id = topics.upsert_topic_entry(uid(), entity_id, date_str, content)
    return jsonify({"ok": True, "entry_id": entry_id})


@app.route("/api/entities/<int:entity_id>/link", methods=["POST"])
def api_entity_link(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not topics.get_entity(uid(), entity_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    to_tag_id = data.get("to_tag_id")
    if not to_tag_id:
        return jsonify({"error": "to_tag_id required"}), 400
    note = (data.get("note") or "").strip() or None
    topics.add_tag_link(uid(), entity_id, int(to_tag_id), note=note, source="user")
    return jsonify({"ok": True})


@app.route("/api/entities/<int:entity_id>/unlink", methods=["POST"])
def api_entity_unlink(entity_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    to_tag_id = (request.get_json(silent=True) or {}).get("to_tag_id")
    if not to_tag_id:
        return jsonify({"error": "to_tag_id required"}), 400
    topics.remove_tag_link(entity_id, int(to_tag_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Tasks API
# ---------------------------------------------------------------------------

@app.route("/api/tasks/intake", methods=["POST"])
def api_task_intake():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    tz = user_tz()
    parsed = llm_service.ai_parse_task(text, datetime.now(tz), tz.key, user_id=uid())
    if data.get("due_at"):
        parsed["due_at"] = data.get("due_at")
    if data.get("priority"):
        parsed["priority"] = data.get("priority")
    if data.get("recurrence_rule"):
        parsed["recurrence_rule"] = data.get("recurrence_rule")
    tid = tasks_db.create_task(uid(), **parsed, source="user")
    return jsonify({"ok": True, "task": tasks_db.get_task(uid(), tid)})


@app.route("/api/tasks/<task_id>", methods=["GET", "POST"])
def api_task_detail(task_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    next_task_id = None
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        old_task = tasks_db.get_task(uid(), task_id)
        tasks_db.update_task(uid(), task_id, **data)
        if (
            data.get("status") == "done"
            and old_task
            and old_task.get("status") != "done"
            and old_task.get("recurrence_rule")
        ):
            updated = tasks_db.get_task(uid(), task_id)
            tz = user_tz()
            next_due = llm_service.ai_parse_recurrence(
                updated["recurrence_rule"],
                tz.key,
                datetime.now(tz).strftime("%Y-%m-%d %H:%M"),
                updated.get("due_at"),
                user_id=uid(),
            )
            if next_due:
                next_task_id = tasks_db.create_next_occurrence(uid(), updated, next_due)
    t = tasks_db.get_task(uid(), task_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    t["tags"] = topics.get_tags_for_task(task_id)
    t["depends_on"] = tasks_db.get_depends_on(task_id)
    if next_task_id:
        t["next_task_id"] = next_task_id
    return jsonify(t)


@app.route("/api/tasks/<task_id>/delete", methods=["POST"])
def api_delete_task(task_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    tasks_db.delete_task(uid(), task_id)
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>/accept", methods=["POST"])
def api_task_accept(task_id):
    """Accept a suggested task — moves it to open status."""
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    task = tasks_db.get_task(uid(), task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    tasks_db.update_task(uid(), task_id, status="open")
    return jsonify({"ok": True, "task": tasks_db.get_task(uid(), task_id)})


@app.route("/api/tasks/<task_id>/reject", methods=["POST"])
def api_task_reject(task_id):
    """Reject a suggested task — soft deletes it."""
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    task = tasks_db.get_task(uid(), task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    tasks_db.soft_delete_task(uid(), task_id)
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>/snooze", methods=["POST"])
def api_task_snooze(task_id):
    """Snooze a suggested task — keeps it suggested but sets a due date for later review."""
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    task = tasks_db.get_task(uid(), task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    due_at = data.get("due_at")
    tasks_db.update_task(uid(), task_id, due_at=due_at)
    return jsonify({"ok": True})


@app.route("/api/tasks/bulk", methods=["POST"])
def api_tasks_bulk():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    task_ids = data.get("task_ids") or []
    action = data.get("action")
    if not task_ids or action not in ("done", "cancelled", "delete"):
        return jsonify({"error": "invalid request"}), 400
    if action == "delete":
        count = tasks_db.bulk_delete(uid(), task_ids)
    else:
        count = tasks_db.bulk_update_status(uid(), task_ids, action)
    return jsonify({"ok": True, "count": count})


@app.route("/api/tasks/<task_id>/tags", methods=["POST"])
def api_task_tags(task_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    tag_id = data.get("tag_id")
    if not tag_id:
        return jsonify({"error": "tag_id required"}), 400
    if action == "add":
        topics.tag_task(uid(), task_id, [int(tag_id)])
    elif action == "remove":
        topics.untag_task(task_id, int(tag_id))
    else:
        return jsonify({"error": "action must be add or remove"}), 400
    return jsonify({"ok": True, "tags": topics.get_tags_for_task(task_id)})


@app.route("/api/tasks/<task_id>/depends", methods=["POST"])
def api_task_depends(task_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    dep_id = (data.get("depends_on_task_id") or "").strip()
    if not dep_id:
        return jsonify({"error": "depends_on_task_id required"}), 400
    if action == "add":
        tasks_db.link_tasks(uid(), task_id, dep_id, kind="depends_on", source="user")
    elif action == "remove":
        tasks_db.unlink_tasks(task_id, dep_id, kind="depends_on")
    else:
        return jsonify({"error": "action must be add or remove"}), 400
    return jsonify({"ok": True})


@app.route("/api/tasks/search", methods=["GET"])
def api_tasks_search():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    return jsonify(tasks_db.search_tasks(uid(), q, exclude_id=request.args.get("exclude") or None, limit=8))


@app.route("/api/search", methods=["GET"])
def api_global_search():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"tasks": [], "topics": [], "entities": []})
    return jsonify({
        "tasks": tasks_db.search_tasks_all_statuses(uid(), q, limit=6),
        "topics": topics.search_tags(uid(), q, kind="topic", limit=6),
        "entities": topics.search_tags(uid(), q, kind="entity", limit=6),
    })


@app.route("/api/tasks/tags/search", methods=["GET"])
def api_tags_search():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    q = (request.args.get("q") or "").strip().lower()
    all_tags = topics.list_all_tags(uid())
    if q:
        all_tags = [t for t in all_tags if q in t["name"].lower()]
    return jsonify(all_tags[:12])


@app.route("/api/tasks/<task_id>/due", methods=["POST"])
def api_task_due(task_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    due_at = (request.get_json(silent=True) or {}).get("due_at") or None
    tasks_db.update_task(uid(), task_id, due_at=due_at)
    return jsonify({"ok": True})


@app.route("/api/tags/filter-data")
def api_tags_filter_data():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "topics": topics.list_topics(uid()),
        "root_topics": topics.list_root_topics(uid()),
        "entities": topics.list_entities(uid()),
    })


@app.route("/api/extraction-items/<int:item_id>/delete", methods=["POST"])
def api_extraction_item_delete(item_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    item = topics.get_extraction_item(uid(), item_id)
    if not item:
        return jsonify({"error": "not found"}), 404
    topics.set_extraction_item_deleted(uid(), item_id, True)
    if item["item_type"] == "task":
        tasks_db.soft_delete_task(uid(), item["item_id"])
    return jsonify({"ok": True})


@app.route("/api/extraction-items/<int:item_id>/restore", methods=["POST"])
def api_extraction_item_restore(item_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    item = topics.get_extraction_item(uid(), item_id)
    if not item:
        return jsonify({"error": "not found"}), 404
    topics.set_extraction_item_deleted(uid(), item_id, False)
    if item["item_type"] == "task":
        tasks_db.restore_task(uid(), item["item_id"])
    return jsonify({"ok": True})


@app.route("/api/entries/<entry_id>/follow-up-chat", methods=["POST"])
def api_entry_followup_chat(entry_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    entry = journal.get_entry_by_id(uid(), entry_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    chat_id = topics.create_chat_unscoped(uid(), f"Entry follow-up {entry['date']}")
    extraction = topics.get_extraction_for_entry(uid(), entry_id)
    context = (
        f"Entry follow-up context.\n\nDate: {entry['date']} {entry['time']}\n\n"
        f"Entry:\n{entry['content']}\n\n"
        f"Extraction summary:\n{(extraction or {}).get('summary') or '(none)'}"
    )
    topics.add_message(chat_id, "user", context)
    return jsonify({"ok": True, "chat_id": chat_id, "redirect": url_for("chat_page_active", chat_id=chat_id)})


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if len(pw) < 6:
            return render_template("reset_password.html", token=token, error="Password must be at least 6 characters.", success=False)
        if pw != pw2:
            return render_template("reset_password.html", token=token, error="Passwords do not match.", success=False)
        user_id = users.consume_reset_token(token)
        if not user_id:
            return render_template("reset_password.html", token=token, error="This reset link is invalid, expired, or already used.", success=False)
        users.reset_password(user_id, pw)
        return render_template("reset_password.html", token=token, error=None, success=True)
    user_id = users.validate_reset_token(token)
    error = None if user_id else "This reset link is invalid, expired, or already used."
    return render_template("reset_password.html", token=token, error=error, success=False)


@app.route("/admin")
def admin_view():
    if not logged_in():
        return redirect(url_for("login"))
    if not users.is_admin(uid()):
        return redirect(url_for("index"))
    all_users = users.list_all_users()
    for u in all_users:
        u["timezone"] = topics.get_setting(u["id"], "timezone", DEFAULT_TZ)
        u["has_api_key"] = u["api_key"] is not None
    return render_template("admin.html", users=all_users)


@app.route("/api/admin/users/<int:user_id>/approve", methods=["POST"])
def api_admin_approve(user_id):
    err = require_admin_json()
    if err:
        return err
    users.approve_user(uid(), user_id)
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>/generate-reset", methods=["POST"])
def api_admin_generate_reset(user_id):
    err = require_admin_json()
    if err:
        return err
    if not users.get_by_id(user_id):
        return jsonify({"error": "not found"}), 404
    token = users.create_reset_token(user_id)
    return jsonify({"ok": True, "reset_url": request.host_url.rstrip("/") + url_for("reset_password", token=token)})


@app.route("/api/admin/users/<int:user_id>/ai-models", methods=["POST"])
def api_admin_user_ai_models(user_id):
    err = require_admin_json()
    if err:
        return err
    if not users.get_by_id(user_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        ai_models.set_user_model_settings(
            user_id,
            regular=(data.get("regular") or None),
            lite=(data.get("lite") or None),
        )
        if "functions" in data and isinstance(data["functions"], dict):
            ai_models.set_func_settings(user_id, data["functions"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "settings": ai_models.get_user_model_settings(user_id),
        "functions": ai_models.get_user_func_settings(user_id),
    })


@app.route("/admin/users/<int:user_id>")
def admin_user_detail(user_id):
    if not logged_in():
        return redirect(url_for("login"))
    if not users.is_admin(uid()):
        return redirect(url_for("index"))
    target_user = users.get_by_id(user_id)
    if not target_user:
        return redirect(url_for("admin_view"))
    days = max(1, min(int(request.args.get("days", 30)), 365))
    start_dt = datetime.now(timezone.utc) - timedelta(days=days)
    start = start_dt.isoformat()
    log_stats = logs_db.user_log_stats(user_id, start=start)
    stats = {
        **log_stats,
        "logins": users.login_count(user_id, start=start),
        "tasks": tasks_db.count_tasks(user_id, start=start),
        "entries": log_stats["completed"],
    }
    ai_usage = llm_service.fetch_user_ai_usage(user_id, start_date=start_dt.strftime("%Y-%m-%d"))
    return render_template(
        "admin_user_detail.html",
        target_user=target_user,
        stats=stats,
        ai_usage=ai_usage,
        days=days,
        model_options=ai_models.preset_options(),
        model_settings=ai_models.get_user_model_settings(user_id),
        func_settings=ai_models.get_user_func_settings(user_id),
        ai_functions=ai_models.AI_FUNCTIONS,
        reasoning_levels=ai_models.REASONING_LEVELS,
    )


@app.route("/admin/users/<int:user_id>/logs")
def admin_user_logs(user_id):
    if not logged_in():
        return redirect(url_for("login"))
    if not users.is_admin(uid()):
        return redirect(url_for("index"))
    target_user = users.get_by_id(user_id)
    if not target_user:
        return redirect(url_for("admin_view"))
    return render_template("admin_logs.html", target_user=target_user, entries=logs_db.list_logs_admin(user_id))


# ---------------------------------------------------------------------------
# Decision Log
# ---------------------------------------------------------------------------

@app.route("/decisions")
def decisions_list():
    if not logged_in():
        return redirect(url_for("login"))
    if not decisions_enabled():
        return redirect(url_for("index"))
    items = decisions_db.list_items(uid())
    tag_map = decisions_db.get_decision_tag_map(uid(), [i["id"] for i in items])
    all_tags = topics.list_all_tags(uid())
    return render_template("decisions.html", items=items, tag_map=tag_map, all_tags=all_tags)


@app.route("/api/decisions", methods=["GET", "POST"])
def api_decisions():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not decisions_enabled():
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        item_type = request.args.get("item_type")
        status = request.args.get("status")
        review_status = request.args.get("review_status")
        try:
            decisions_db.validate_item_fields(
                {k: v for k, v in {
                    "item_type": item_type,
                    "status": status,
                    "review_status": review_status,
                }.items() if v},
                partial=True,
            )
        except decisions_db.DecisionValidationError as e:
            return _json_validation_error(e)
        items = decisions_db.list_items(uid(), item_type=item_type, status=status,
                                        review_status=review_status)
        tag_map = decisions_db.get_decision_tag_map(uid(), [i["id"] for i in items])
        for item in items:
            item["tags"] = tag_map.get(item["id"], [])
        return jsonify({"items": items})
    data = request.get_json(silent=True) or {}
    item_type = data.get("item_type")
    title = (data.get("title") or "").strip()
    if not item_type or not title:
        return jsonify({"error": "item_type and title required"}), 400
    try:
        item_id = decisions_db.create_item(
            uid(),
            item_type=item_type,
            title=title,
            content=data.get("content"),
            rationale=data.get("rationale"),
            alternatives=data.get("alternatives"),
            status=data.get("status", "open"),
            review_status=data.get("review_status", "accepted"),
            source=data.get("source", "manual"),
            confidence=data.get("confidence"),
            review_at=data.get("review_at"),
            reject_duplicates=True,
        )
    except decisions_db.DuplicateDecisionError:
        return jsonify({"error": "duplicate decision log item"}), 409
    except decisions_db.DecisionValidationError as e:
        return _json_validation_error(e)
    tag_ids = data.get("tag_ids") or []
    if tag_ids:
        decisions_db.tag_decision(uid(), item_id, tag_ids)
    return jsonify({"id": item_id}), 201


@app.route("/api/decisions/<item_id>", methods=["GET", "PATCH", "DELETE"])
def api_decision_detail(item_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not decisions_enabled():
        return jsonify({"error": "forbidden"}), 403
    item = decisions_db.get_item(uid(), item_id)
    if not item:
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        item["tags"] = decisions_db.get_tags_for_decision(item_id)
        return jsonify(item)
    if request.method == "DELETE":
        decisions_db.soft_delete_item(uid(), item_id)
        return jsonify({"ok": True})
    # PATCH
    data = request.get_json(silent=True) or {}
    try:
        decisions_db.update_item(uid(), item_id, **data)
    except decisions_db.DuplicateDecisionError:
        return jsonify({"error": "duplicate decision log item"}), 409
    except decisions_db.DecisionValidationError as e:
        return _json_validation_error(e)
    # Sync tags if provided
    if "tag_ids" in data:
        new_ids = set(int(t) for t in (data["tag_ids"] or []))
        current = {t["id"] for t in decisions_db.get_tags_for_decision(item_id)}
        for add_id in new_ids - current:
            decisions_db.tag_decision(uid(), item_id, [add_id])
        for rem_id in current - new_ids:
            decisions_db.untag_decision(item_id, rem_id)
    return jsonify({"ok": True})


@app.route("/api/decisions/<item_id>/accept", methods=["POST"])
def api_decision_accept(item_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not decisions_enabled():
        return jsonify({"error": "forbidden"}), 403
    decisions_db.accept_suggestion(uid(), item_id)
    return jsonify({"ok": True})


@app.route("/api/decisions/<item_id>/dismiss", methods=["POST"])
def api_decision_dismiss(item_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not decisions_enabled():
        return jsonify({"error": "forbidden"}), 403
    decisions_db.dismiss_suggestion(uid(), item_id)
    return jsonify({"ok": True})


@app.route("/api/decisions/<item_id>/tags", methods=["POST", "DELETE"])
def api_decision_tags(item_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not decisions_enabled():
        return jsonify({"error": "forbidden"}), 403
    if not decisions_db.get_item(uid(), item_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    tag_id = data.get("tag_id")
    if not tag_id:
        return jsonify({"error": "tag_id required"}), 400
    if request.method == "POST":
        decisions_db.tag_decision(uid(), item_id, [int(tag_id)])
    else:
        decisions_db.untag_decision(item_id, int(tag_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Trackers
# ---------------------------------------------------------------------------

@app.route("/trackers")
def trackers_list():
    if not logged_in():
        return redirect(url_for("login"))
    tz = user_tz()
    today = datetime.now(tz).strftime("%Y-%m-%d")
    all_trackers = trackers_db.list_trackers(uid())
    for t in all_trackers:
        entries = trackers_db.list_entries(uid(), t["id"], limit=31)
        t["entries_json"] = json.dumps([
            {"date": e["entry_date"], "value": json.loads(e["value_json"]) if e["value_json"] is not None else None}
            for e in entries
        ])
        commentary = trackers_db.get_commentary(uid(), t["id"])
        t["commentary"] = commentary["commentary"] if commentary else None
    return render_template(
        "trackers.html",
        trackers=all_trackers,
        today=today,
    )


@app.route("/trackers/new")
def tracker_create_page():
    if not logged_in():
        return redirect(url_for("login"))
    return render_template("tracker_create.html")


@app.route("/trackers/snapshots")
def tracker_snapshots_page():
    if not logged_in():
        return redirect(url_for("login"))
    tz = user_tz()
    today = datetime.now(tz).strftime("%Y-%m-%d")
    snapshots = trackers_db.list_upcoming_snapshots(uid(), today)
    from itertools import groupby
    grouped = []
    for date, group in groupby(snapshots, key=lambda s: s["entry_date"]):
        grouped.append({"date": date, "entries": list(group)})
    return render_template("tracker_snapshots.html", grouped=grouped, today=today)


@app.route("/trackers/<tracker_id>")
def tracker_detail_page(tracker_id):
    if not logged_in():
        return redirect(url_for("login"))
    tracker = trackers_db.get_tracker(uid(), tracker_id)
    if not tracker:
        return redirect(url_for("trackers_list"))
    tz = user_tz()
    today = datetime.now(tz).strftime("%Y-%m-%d")
    # Default range: last 30 days
    date_from = request.args.get("from") or (datetime.now(tz) - timedelta(days=29)).strftime("%Y-%m-%d")
    date_to = request.args.get("to") or today
    entries = trackers_db.list_entries(uid(), tracker_id, date_from=date_from, date_to=date_to)
    commentary = trackers_db.get_commentary(uid(), tracker_id)
    # Refresh commentary if stale
    if tracker.get("ai_commentary_instructions") and trackers_db.commentary_is_stale(tracker_id, uid()):
        try:
            llm_service.ai_tracker_commentary(uid(), tracker_id)
            commentary = trackers_db.get_commentary(uid(), tracker_id)
        except Exception:
            pass
    return render_template(
        "tracker_detail.html",
        tracker=tracker,
        entries=entries,
        entries_json=json.dumps([
            {"date": e["entry_date"], "value": json.loads(e["value_json"]) if e["value_json"] is not None else None,
             "skipped": bool(e["skipped"]), "id": e["id"]}
            for e in entries
        ]),
        commentary=commentary,
        today=today,
        date_from=date_from,
        date_to=date_to,
    )


# API ---

@app.route("/api/trackers", methods=["GET", "POST"])
def api_trackers():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "GET":
        return jsonify({"trackers": trackers_db.list_trackers(uid())})
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    tracker_type = (data.get("type") or "yes_no").strip()
    frequency = (data.get("frequency") or "Daily").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        cron_expression = llm_service.ai_translate_frequency(frequency, uid())
        number_min = data.get("number_min")
        number_max = data.get("number_max")
        tracker_id = trackers_db.create_tracker(
            uid(),
            name=name,
            type=tracker_type,
            frequency=frequency,
            cron_expression=cron_expression,
            capture_instructions=data.get("capture_instructions"),
            ai_commentary_instructions=data.get("ai_commentary_instructions"),
            number_min=float(number_min) if number_min is not None else None,
            number_max=float(number_max) if number_max is not None else None,
        )
    except trackers_db.TrackerError as e:
        return _json_validation_error(e)
    return jsonify({"id": tracker_id}), 201


@app.route("/api/trackers/reorder", methods=["POST"])
def api_trackers_reorder():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    order = data.get("order") or []
    trackers_db.reorder_trackers(uid(), order)
    return jsonify({"ok": True})


@app.route("/api/trackers/<tracker_id>", methods=["GET", "PATCH", "DELETE"])
def api_tracker_detail(tracker_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    tracker = trackers_db.get_tracker(uid(), tracker_id)
    if not tracker:
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        return jsonify(tracker)
    if request.method == "DELETE":
        trackers_db.archive_tracker(uid(), tracker_id)
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    # If frequency changed, re-translate cron
    if "frequency" in data and data["frequency"] != tracker.get("frequency"):
        data["cron_expression"] = llm_service.ai_translate_frequency(data["frequency"], uid())
    try:
        trackers_db.update_tracker(uid(), tracker_id, **data)
    except trackers_db.TrackerError as e:
        return _json_validation_error(e)
    return jsonify({"ok": True})


@app.route("/api/trackers/<tracker_id>/entries", methods=["GET", "POST"])
def api_tracker_entries(tracker_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not trackers_db.get_tracker(uid(), tracker_id):
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        date_from = request.args.get("from")
        date_to = request.args.get("to")
        entries = trackers_db.list_entries(uid(), tracker_id, date_from=date_from, date_to=date_to)
        return jsonify({"entries": entries})
    data = request.get_json(silent=True) or {}
    entry_date = (data.get("entry_date") or "").strip()
    if not entry_date:
        return jsonify({"error": "entry_date required"}), 400
    raw_value = data.get("value")
    value_json = json.dumps(raw_value) if raw_value is not None else None
    eid = trackers_db.upsert_entry(uid(), tracker_id, entry_date, value_json)
    return jsonify({"id": eid}), 201


@app.route("/api/trackers/<tracker_id>/entries/<entry_id>", methods=["PATCH"])
def api_tracker_entry_update(tracker_id, entry_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    entry = trackers_db.get_entry(uid(), entry_id)
    if not entry or entry["tracker_id"] != tracker_id:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    raw_value = data.get("value")
    value_json = json.dumps(raw_value) if raw_value is not None else None
    trackers_db.update_entry(uid(), entry_id, value_json)
    return jsonify({"ok": True})


@app.route("/api/trackers/<tracker_id>/entries/<entry_id>/skip", methods=["POST"])
def api_tracker_entry_skip(tracker_id, entry_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    entry = trackers_db.get_entry(uid(), entry_id)
    if not entry or entry["tracker_id"] != tracker_id:
        return jsonify({"error": "not found"}), 404
    trackers_db.skip_entry(uid(), entry_id)
    return jsonify({"ok": True})


@app.route("/api/trackers/<tracker_id>/upcoming", methods=["POST"])
def api_tracker_upcoming_entry(tracker_id):
    """Create a pre-filled or pre-skipped upcoming entry for a given date."""
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    if not trackers_db.get_tracker(uid(), tracker_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    entry_date = (data.get("entry_date") or "").strip()
    if not entry_date:
        return jsonify({"error": "entry_date required"}), 400
    skipped = bool(data.get("skipped"))
    value = data.get("value")
    value_json = json.dumps(value) if value is not None else None
    eid = trackers_db.upsert_entry(uid(), tracker_id, entry_date, value_json,
                                   source="manual", skipped=skipped)
    return jsonify({"id": eid})


@app.route("/api/trackers/snapshots", methods=["GET"])
def api_trackers_snapshots():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    tz = user_tz()
    today = datetime.now(tz).strftime("%Y-%m-%d")
    snapshots = trackers_db.list_pending_snapshots(uid(), today)
    return jsonify({"snapshots": snapshots})


@app.route("/api/trackers/<tracker_id>/commentary", methods=["GET"])
def api_tracker_commentary(tracker_id):
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    tracker = trackers_db.get_tracker(uid(), tracker_id)
    if not tracker:
        return jsonify({"error": "not found"}), 404
    refresh = request.args.get("refresh") == "1"
    if refresh or trackers_db.commentary_is_stale(tracker_id, uid()):
        try:
            llm_service.ai_tracker_commentary(uid(), tracker_id)
        except Exception:
            logger.exception("Commentary generation failed for tracker %s", tracker_id)
    commentary = trackers_db.get_commentary(uid(), tracker_id)
    return jsonify({"commentary": commentary})


@app.route("/api/trackers/capture", methods=["POST"])
def api_trackers_capture():
    if not logged_in():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    as_of_date = data.get("as_of_date") or datetime.now(user_tz()).strftime("%Y-%m-%d")
    eval_time = topics.get_setting(uid(), "tracker_eval_time", "00:00")
    return jsonify(llm_service.run_tracker_cron(uid(), as_of_date, eval_time=eval_time))


# ---------------------------------------------------------------------------
# Tracker scheduler
# ---------------------------------------------------------------------------

_TRACKER_SCHEDULER_STARTED = False
_TRACKER_SCHEDULER_LOCK = threading.Lock()


def _parse_tracker_eval_time(value: str | None) -> tuple[int, int]:
    try:
        hour_s, minute_s = (value or "00:00").split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except Exception:
        pass
    return 0, 0


def _tracker_batch_dates(now_utc: datetime, user_id: int) -> tuple[str, str, str, datetime] | None:
    tz_name = topics.get_setting(user_id, "timezone", DEFAULT_TZ) or DEFAULT_TZ
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TZ)
    now_local = now_utc.astimezone(tz)
    eval_time = topics.get_setting(user_id, "tracker_eval_time", "00:00") or "00:00"
    hour, minute = _parse_tracker_eval_time(eval_time)
    eval_today = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_local < eval_today:
        return None

    capture_day = topics.get_setting(user_id, "tracker_capture_day", "previous") or "previous"
    local_date = now_local.date()
    if capture_day == "same":
        entry_date = local_date
        interval_end = eval_today
    else:
        entry_date = local_date - timedelta(days=1)
        interval_end = eval_today
        capture_day = "previous"

    return local_date.isoformat(), entry_date.isoformat(), capture_day, interval_end


def run_scheduled_tracker_batches(now_utc: datetime | None = None) -> list[dict]:
    now_utc = now_utc or datetime.now(timezone.utc)
    results = []
    if not _TRACKER_SCHEDULER_LOCK.acquire(blocking=False):
        return results
    try:
        for user in users.list_all_users():
            if user.get("disabled_at"):
                continue
            user_id = user["id"]
            batch = _tracker_batch_dates(now_utc, user_id)
            if not batch:
                continue
            local_run_date, entry_date, capture_day, interval_end = batch
            eval_time = topics.get_setting(user_id, "tracker_eval_time", "00:00") or "00:00"
            run_key = f"{local_run_date}|{entry_date}|{capture_day}|{eval_time}"
            if topics.get_setting(user_id, "tracker_capture_last_run", "") == run_key:
                continue

            logger.info(
                "Running tracker capture batch user_id=%s entry_date=%s interval_end=%s",
                user_id,
                entry_date,
                interval_end.isoformat(),
            )
            result = llm_service.run_tracker_cron(
                user_id,
                entry_date,
                interval_end=interval_end.isoformat(),
                eval_time=eval_time,
            )
            topics.set_setting(user_id, "tracker_capture_last_run", run_key)
            results.append({"user_id": user_id, "entry_date": entry_date, **result})
    finally:
        _TRACKER_SCHEDULER_LOCK.release()
    return results


def _tracker_scheduler_loop():
    interval = int(os.environ.get("TRACKER_SCHEDULER_INTERVAL_SECONDS", "60"))
    while True:
        try:
            run_scheduled_tracker_batches()
        except Exception:
            logger.exception("Scheduled tracker capture failed")
        time.sleep(max(15, interval))


def start_tracker_scheduler():
    global _TRACKER_SCHEDULER_STARTED
    if _TRACKER_SCHEDULER_STARTED:
        return
    if os.environ.get("TRACKER_SCHEDULER_DISABLED") == "1":
        return
    if len(sys.argv) > 1 and sys.argv[1] == "tracker-cron":
        return
    if "unittest" in sys.modules:
        return
    _TRACKER_SCHEDULER_STARTED = True
    threading.Thread(target=_tracker_scheduler_loop, daemon=True, name="tracker-scheduler").start()


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

start_tracker_scheduler()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "tracker-cron":
        as_of_date = sys.argv[2] if len(sys.argv) > 2 else datetime.now(ZoneInfo(DEFAULT_TZ)).strftime("%Y-%m-%d")
        for user in users.list_all_users():
            if user.get("disabled_at"):
                continue
            eval_time = topics.get_setting(user["id"], "tracker_eval_time", "00:00")
            print(json.dumps({"user_id": user["id"], **llm_service.run_tracker_cron(user["id"], as_of_date, eval_time=eval_time)}))
    else:
        threading.Thread(target=llm_service.backfill_tag_embeddings, daemon=True).start()
        app.run(host="0.0.0.0", port=8002, debug=os.environ.get("FLASK_DEBUG") == "1")
