from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import secrets
from pathlib import Path

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import achievements
import db
import planner
import calendar_sync
import streaks
from ai import build_user_context, query_terra_ai
from auth import get_authenticated_user_id, require_auth
from guardrails import check_rate_limit, validate_scope

BASE_DIR = Path(__file__).resolve().parent.parent

app = Flask(
    __name__,
    template_folder=BASE_DIR / "templates",
    static_folder=BASE_DIR / "static",
)

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be configured before starting Smart Planner.")

app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
)

ALLOWED_FOCUS_MODES = {"pomodoro", "deepwork", "quickburst", "examcrunch", "custom"}

db.init_db()
app.register_blueprint(planner.planner_bp)
app.register_blueprint(calendar_sync.calendar_bp)


@app.teardown_appcontext
def close_database(exception=None):
    db.close_db(exception)


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def protect_state_changes():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    expected = session.get("csrf_token") or csrf_token()
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")

    if not secrets.compare_digest(str(supplied or ""), str(expected or "")):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "CSRF validation failed.", "code": "CSRF_FAILED"}), 403
        flash("Your form expired. Please try again.")
        fallback = "dashboard" if get_authenticated_user_id() is not None else "login"
        return redirect(request.referrer or url_for(fallback))
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    )
    return response


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Not found.", "code": "NOT_FOUND"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    app.logger.exception("Unhandled server error")
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "An internal server error occurred.", "code": "INTERNAL_ERROR"}), 500
    return render_template("404.html"), 500


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.exception("Unexpected error")
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "An unexpected error occurred.", "code": "UNEXPECTED_ERROR"}), 500
    return render_template("404.html"), 500


def _json_error(message, code, status):
    return jsonify({"error": message, "code": code}), status


def _auth_redirect():
    return redirect(url_for("login"))


def _parse_positive_minutes(value, maximum=1440):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if 1 <= minutes <= maximum else None


def _utc_now():
    return datetime.now(timezone.utc)


def _utc_today():
    return _utc_now().date().isoformat()


def _parse_utc_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Timezone Utilities
# ---------------------------------------------------------------------------

def _get_user_timezone(user_id=None):
    """Resolve user timezone from explicit ID, request header, or fallback to UTC."""
    if user_id:
        user = _user_row(user_id)
        if user and user["timezone"]:
            try:
                return ZoneInfo(user["timezone"])
            except ZoneInfoNotFoundError:
                pass

    # Client-supplied timezone header support (e.g. 'America/New_York')
    header_tz = request.headers.get("X-Timezone")
    if header_tz:
        try:
            return ZoneInfo(header_tz)
        except ZoneInfoNotFoundError:
            pass

    return timezone.utc


def _user_now(user_id=None):
    """Return the current localized datetime for the specified user."""
    return datetime.now(_get_user_timezone(user_id))


def _user_today(user_id=None):
    """Return the current local date (YYYY-MM-DD) for the specified user."""
    return _user_now(user_id).date().isoformat()


def _user_day_utc_bounds(user_id=None, target_date=None):
    """Return UTC start and end ISO strings representing a user's local day.
    
    This ensures queries checking 'today' correctly capture the exact 24-hour UTC span
    that corresponds to midnight-to-midnight in the user's specific local timezone.
    """
    tz = _get_user_timezone(user_id)
    if target_date is None:
        local_date = datetime.now(tz).date()
    elif isinstance(target_date, str):
        local_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    else:
        local_date = target_date

    start_local = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=1)

    start_utc = start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = end_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return start_utc, end_utc


def _user_row(user_id):
    return db.get_db().execute(
        """SELECT id, username, password_hash, display_name,
                  default_focus_mins, break_mins, ai_tone, timezone
           FROM users WHERE id = ?""",
        (user_id,),
    ).fetchone()


def _dashboard_suggestion(active_tasks, today_events, focus_today):
    if not active_tasks and not today_events:
        return "Your plan is clear right now. Add a task or schedule a focus block when you are ready."
    if active_tasks:
        next_task = sorted(
            active_tasks,
            key=lambda task: (task.get("priority", 2), task.get("due_date") or "9999-12-31", task["id"]),
        )[0]
        if focus_today == 0:
            return f"Start with {next_task['title']} and log one focused session before switching tasks."
        return f"Keep momentum by moving next to {next_task['title']} while your focus streak is warm."
    return "Your task list is light today. Protect your calendar commitments and use open time for review."


# ---------------------------------------------------------------------------
# Public / auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    if get_authenticated_user_id() is not None:
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/about")
def about():
    return render_template("about.html")


def _password_is_secure(password):
    return (
        len(password) >= 12
        and any(c.isupper() for c in password)
        and any(c.islower() for c in password)
        and any(c.isdigit() for c in password)
        and any(not c.isalnum() for c in password)
    )


def _email_is_plausible(email):
    return (
        isinstance(email, str)
        and 3 <= len(email) <= 254
        and "@" in email
        and "." in email.rsplit("@", 1)[-1]
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if get_authenticated_user_id() is not None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        terms_accepted = request.form.get("terms_accepted") == "true"
        tz_name = request.form.get("timezone", "UTC").strip()
        
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz_name = "UTC"

        if not username or not email or not password or not confirm_password:
            flash("Please fill in all fields.")
            return redirect(url_for("signup"))
        if len(username) > 50:
            flash("Username must be 50 characters or fewer.")
            return redirect(url_for("signup"))
        if not _email_is_plausible(email):
            flash("Please enter a valid email address.")
            return redirect(url_for("signup"))
        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("signup"))
        if not terms_accepted:
            flash("Please accept the Terms of Service to continue.")
            return redirect(url_for("signup"))
        if not _password_is_secure(password):
            flash("Password must contain 12+ characters, uppercase, lowercase, number, and symbol.")
            return redirect(url_for("signup"))

        conn = db.get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, email, password_hash, timezone) VALUES (?, ?, ?, ?)",
                (username, email, generate_password_hash(password), tz_name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            flash("Username already exists.")
            return redirect(url_for("signup"))

        session.clear()
        session.permanent = True
        session["user_id"] = cursor.lastrowid
        session["username"] = username
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_authenticated_user_id() is not None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = db.get_db().execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not username or not password or not row or not check_password_hash(row["password_hash"], password):
            flash("Invalid username or password.")
            return redirect(url_for("login"))

        session.clear()
        session.permanent = True
        session["user_id"] = row["id"]
        session["username"] = row["username"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _auth_redirect()

    conn = db.get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_profile":
            display_name = request.form.get("display_name", "").strip()[:100]
            tz_name = request.form.get("timezone", "UTC").strip()
            
            try:
                ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                return _json_error("Invalid timezone provided.", "INVALID_TIMEZONE", 400)

            conn.execute(
                "UPDATE users SET display_name = ?, timezone = ? WHERE id = ?",
                (display_name or None, tz_name, user_id),
            )
        elif action == "save_focus_preferences":
            focus = _parse_positive_minutes(request.form.get("focus_mins"), 240)
            break_mins = _parse_positive_minutes(request.form.get("break_mins"), 60)
            if focus is None or break_mins is None:
                return _json_error("Invalid timer preference.", "INVALID_PREFERENCE", 400)
            conn.execute(
                """UPDATE users
                   SET default_focus_mins = ?, break_mins = ?
                   WHERE id = ?""",
                (focus, break_mins, user_id),
            )
        elif action == "save_ai_preferences":
            tone = request.form.get("ai_tone", "balanced")
            if tone not in {"supportive", "balanced", "strict"}:
                return _json_error("Invalid AI tone.", "INVALID_PREFERENCE", 400)
            conn.execute(
                """UPDATE users
                   SET ai_tone = ?
                   WHERE id = ?""",
                (tone, user_id),
            )
        else:
            return _json_error("Unknown settings action.", "INVALID_ACTION", 400)
        conn.commit()
        flash("Settings saved successfully.", "success")
        return redirect(url_for("settings"))

    user = _user_row(user_id)
    return render_template(
        "settings.html",
        display_name=user["display_name"] or user["username"],
        default_focus_mins=user["default_focus_mins"] or 25,
        break_mins=user["break_mins"] or 5,
        ai_tone=user["ai_tone"] or "balanced",
        user_timezone=user["timezone"] or "UTC",
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _auth_redirect()

    # User-localized datetime calculations
    user_tz = _get_user_timezone(user_id)
    now = datetime.now(user_tz)
    greeting_time = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    current_date_str = now.strftime("%A, %b %d")
    today_str = now.strftime("%Y-%m-%d")
    
    start_utc, end_utc = _user_day_utc_bounds(user_id, today_str)
    conn = db.get_db()

    tasks = conn.execute(
        """SELECT * FROM planner_tasks
           WHERE user_id = ? AND is_deleted = 0 AND parent_id IS NULL
           ORDER BY completed ASC,
                    COALESCE(due_date, '9999-12-31') ASC,
                    priority ASC,
                    id ASC""",
        (user_id,),
    ).fetchall()
    task_dicts = [dict(row) for row in tasks]
    active_tasks = [task for task in task_dicts if not task["completed"]]

    upcoming_events = [
        dict(row) for row in conn.execute(
            """SELECT id, title, start_time, end_time, is_all_day
               FROM events
              WHERE user_id = ? AND is_deleted = 0 AND start_time >= ?
              ORDER BY start_time ASC LIMIT 10""",
            (user_id, start_utc),
        ).fetchall()
    ]

    today_events = []
    for row in conn.execute(
        """SELECT id, title, start_time, end_time, is_all_day
           FROM events
          WHERE user_id = ? AND is_deleted = 0 
            AND start_time >= ? AND start_time < ?
          ORDER BY start_time ASC""",
        (user_id, start_utc, end_utc),
    ).fetchall():
        event = dict(row)
        event["start_dt"] = None
        if not event["is_all_day"]:
            try:
                # Convert UTC stored timestamp to user local time for display
                dt_utc = datetime.fromisoformat(event["start_time"].replace("Z", "+00:00"))
                if dt_utc.tzinfo is None:
                    dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                event["start_dt"] = dt_utc.astimezone(user_tz)
            except (TypeError, ValueError):
                event["start_dt"] = None
        today_events.append(event)

    focus_today = conn.execute(
        """SELECT COALESCE(SUM(duration_minutes), 0) AS minutes
           FROM focus_sessions
          WHERE user_id = ? AND status = 'completed'
            AND completed_at >= ? AND completed_at < ?""",
        (user_id, start_utc, end_utc),
    ).fetchone()["minutes"]

    total = len(task_dicts)
    completed = sum(1 for task in task_dicts if task["completed"])
    completion_rate = round(completed / total * 100, 1) if total else 0.0
    priority_tasks = sorted(
        active_tasks,
        key=lambda task: (task.get("priority", 2), task.get("due_date") or "9999-12-31", task["id"]),
    )[:5]

    return render_template(
        "dashboard.html",
        greeting_time=greeting_time,
        current_date_str=current_date_str,
        today_tasks=active_tasks,
        next_up_task=active_tasks[0] if active_tasks else None,
        tasks_remaining_count=len(active_tasks),
        estimated_workload_mins=sum(task.get("estimated_minutes") or 0 for task in active_tasks),
        upcoming_events=upcoming_events,
        today_events=today_events,
        today_events_count=len(today_events),
        priority_tasks=priority_tasks,
        pending_tasks_count=len(active_tasks),
        today_focus_mins=focus_today,
        current_streak=streaks.calculate_streaks(user_id)["current_streak"],
        completion_rate=completion_rate,
        ai_suggestion_text=_dashboard_suggestion(active_tasks, today_events, focus_today),
    )


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")



# ---------------------------------------------------------------------------
# Focus / Study page
# ---------------------------------------------------------------------------

@app.route("/study")
@app.route("/focus")
def study():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _auth_redirect()
    rows = db.get_db().execute(
        """SELECT id, title, subject
           FROM planner_tasks
          WHERE user_id = ? AND is_deleted = 0 AND completed = 0
          ORDER BY COALESCE(due_date, '9999-12-31') ASC, priority ASC, id ASC""",
        (user_id,),
    ).fetchall()
    user = _user_row(user_id)
    return render_template(
        "study.html",
        planner_tasks=[dict(row) for row in rows],
        selected_task_id=request.args.get("task_id", type=int),
        default_focus_mins=user["default_focus_mins"] or 25,
        break_mins=user["break_mins"] or 5,
    )


@app.route("/focus/start", methods=["POST"])
def start_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", "pomodoro")).strip().lower()[:30] or "pomodoro"
    if mode not in ALLOWED_FOCUS_MODES:
        mode = "pomodoro"

    task_id = data.get("task_id")
    if task_id in (None, "", 0):
        task_id = None
    else:
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return _json_error("Invalid task ID.", "INVALID_TASK_ID", 400)
        if not db.get_db().execute(
            "SELECT id FROM planner_tasks WHERE id = ? AND user_id = ? AND is_deleted = 0 AND completed = 0",
            (task_id, user_id),
        ).fetchone():
            return _json_error("Task not found or permission denied.", "TASK_NOT_FOUND", 404)

    conn = db.get_db()
    active = conn.execute(
        "SELECT id FROM focus_sessions WHERE user_id = ? AND status = 'active' LIMIT 1",
        (user_id,),
    ).fetchone()
    if active:
        return _json_error("A Focus session is already active.", "FOCUS_ALREADY_ACTIVE", 409)

    now = _utc_now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """INSERT INTO focus_sessions
           (user_id, task_id, started_at, completed_at, duration_minutes, status, mode)
           VALUES (?, ?, ?, NULL, 0, 'active', ?)""",
        (user_id, task_id, now, mode),
    )
    conn.commit()
    return jsonify({"success": True, "session_id": cursor.lastrowid, "started_at": now})


@app.route("/focus/active")
def active_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    row = db.get_db().execute(
        """SELECT id, task_id, started_at, mode, paused_at, total_paused_seconds
           FROM focus_sessions WHERE user_id = ? AND status = 'active'
           ORDER BY started_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    return jsonify({"active": dict(row) if row else None})


@app.route("/focus/pause", methods=["POST"])
def pause_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    data = request.get_json(silent=True) or {}
    try:
        session_id = int(data.get("session_id"))
    except (TypeError, ValueError):
        return _json_error("A valid focus session ID is required.", "INVALID_SESSION_ID", 400)

    conn = db.get_db()
    now = _utc_now().strftime("%Y-%m-%d %H:%M:%S")
    changed = conn.execute(
        """UPDATE focus_sessions
              SET paused_at = ?
            WHERE id = ? AND user_id = ? AND status = 'active' AND paused_at IS NULL""",
        (now, session_id, user_id),
    ).rowcount
    conn.commit()
    if not changed:
        return _json_error("Focus session is not active or is already paused.", "SESSION_NOT_PAUSABLE", 409)
    return jsonify({"success": True, "session_id": session_id, "paused_at": now})


@app.route("/focus/resume", methods=["POST"])
def resume_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    data = request.get_json(silent=True) or {}
    try:
        session_id = int(data.get("session_id"))
    except (TypeError, ValueError):
        return _json_error("A valid focus session ID is required.", "INVALID_SESSION_ID", 400)

    conn = db.get_db()
    row = conn.execute(
        """SELECT paused_at, total_paused_seconds FROM focus_sessions
            WHERE id = ? AND user_id = ? AND status = 'active'""",
        (session_id, user_id),
    ).fetchone()
    if not row or not row["paused_at"]:
        return _json_error("Focus session is not currently paused.", "SESSION_NOT_PAUSED", 409)

    paused_since = _parse_utc_timestamp(row["paused_at"])
    pause_duration = max(0, (_utc_now() - paused_since).total_seconds()) if paused_since else 0
    new_total_paused = (row["total_paused_seconds"] or 0) + round(pause_duration)

    conn.execute(
        """UPDATE focus_sessions SET paused_at = NULL, total_paused_seconds = ?
            WHERE id = ? AND user_id = ?""",
        (new_total_paused, session_id, user_id),
    )
    conn.commit()
    return jsonify({"success": True, "session_id": session_id, "total_paused_seconds": new_total_paused})


@app.route("/focus/complete", methods=["POST"])
def complete_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    note = str(data.get("note", "")).strip()
    if len(note) > 10_000:
        return _json_error("Note exceeds maximum length of 10 000 characters.", "NOTE_TOO_LONG", 400)
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return _json_error("A valid focus session ID is required.", "INVALID_SESSION_ID", 400)

    conn = db.get_db()
    row = conn.execute(
        """SELECT id, started_at, status, paused_at, total_paused_seconds, duration_minutes
           FROM focus_sessions WHERE id = ? AND user_id = ?""",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return _json_error("Focus session not found.", "SESSION_NOT_FOUND", 404)
    if row["status"] == "completed":
        return jsonify({
            "success": True,
            "already": "completed",
            "session_id": session_id,
            "minutes": row["duration_minutes"],
            "message": "This Focus session was already completed.",
        }), 200
    if row["status"] == "abandoned":
        return jsonify({
            "success": False,
            "already": "abandoned",
            "session_id": session_id,
            "message": "This Focus session was already abandoned and can't be completed.",
        }), 409

    started = _parse_utc_timestamp(row["started_at"])
    if started is None:
        return _json_error("Stored focus start time is invalid.", "SESSION_TIME_INVALID", 500)

    now = _utc_now()

    if now < started:
        return _json_error(
            "Session completion time is before start time.",
            "SESSION_TIME_INVALID", 400,
        )

    total_paused_seconds = row["total_paused_seconds"] or 0
    if row["paused_at"]:
        paused_since = _parse_utc_timestamp(row["paused_at"])
        if paused_since:
            total_paused_seconds += max(0, (now - paused_since).total_seconds())

    elapsed_seconds = max(0, (now - started).total_seconds() - total_paused_seconds)
    elapsed_minutes = max(1, round(elapsed_seconds / 60))

    updated = conn.execute(
        """UPDATE focus_sessions
              SET completed_at = ?, duration_minutes = ?, status = 'completed', paused_at = NULL,
                  note = COALESCE(NULLIF(?, ''), note)
            WHERE id = ? AND user_id = ? AND status = 'active'""",
        (now.strftime("%Y-%m-%d %H:%M:%S"), elapsed_minutes, note, session_id, user_id),
    ).rowcount
    conn.commit()

    if not updated:
        return jsonify({
            "success": True,
            "already": "completed",
            "session_id": session_id,
            "message": "This Focus session was already completed.",
        }), 200

    achievements.check_achievements(user_id)
    return jsonify({
        "success": True,
        "session_id": session_id,
        "minutes": elapsed_minutes,
        "message": "Focus session completed successfully.",
    })


@app.route("/focus/abandon", methods=["POST"])
def abandon_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    data = request.get_json(silent=True) or {}
    try:
        session_id = int(data.get("session_id"))
    except (TypeError, ValueError):
        return _json_error("A valid focus session ID is required.", "INVALID_SESSION_ID", 400)

    conn = db.get_db()
    changed = conn.execute(
        """UPDATE focus_sessions
              SET status = 'abandoned', completed_at = ?, paused_at = NULL
            WHERE id = ? AND user_id = ? AND status = 'active'""",
        (_utc_now().strftime("%Y-%m-%d %H:%M:%S"), session_id, user_id),
    ).rowcount
    conn.commit()
    if not changed:
        existing = conn.execute(
            "SELECT status FROM focus_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if existing and existing["status"] in ("completed", "abandoned"):
            return jsonify({
                "success": True,
                "already": existing["status"],
                "session_id": session_id,
                "message": f"This Focus session was already {existing['status']}.",
            }), 200
        return _json_error("Focus session was not active.", "SESSION_NOT_ACTIVE", 409)
    return jsonify({"success": True, "session_id": session_id, "status": "abandoned"})


# ---------------------------------------------------------------------------
# Focus notes
# ---------------------------------------------------------------------------

@app.route("/focus/notes", methods=["GET"])
def get_focus_notes():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    row = db.get_db().execute(
        "SELECT focus_notes FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    note = row["focus_notes"] if row else ""
    return jsonify({"note": note or ""})


@app.route("/focus/notes", methods=["POST"])
def save_focus_notes():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    data = request.get_json(silent=True) or {}
    note = str(data.get("note", ""))
    if len(note) > 10_000:
        return _json_error("Note exceeds maximum length of 10 000 characters.", "NOTE_TOO_LONG", 400)
    conn = db.get_db()
    conn.execute("UPDATE users SET focus_notes = ? WHERE id = ?", (note, user_id))
    conn.commit()
    return jsonify({"success": True, "note": note})


@app.route("/focus/session/<int:session_id>/note", methods=["GET", "POST"])
def focus_session_note(session_id):
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    conn = db.get_db()
    row = conn.execute(
        "SELECT id, note FROM focus_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return _json_error("Focus session not found.", "SESSION_NOT_FOUND", 404)

    if request.method == "GET":
        return jsonify({"session_id": session_id, "note": row["note"] or ""})

    data = request.get_json(silent=True) or {}
    note = str(data.get("note", ""))
    if len(note) > 10_000:
        return _json_error("Note exceeds maximum length of 10 000 characters.", "NOTE_TOO_LONG", 400)
    conn.execute(
        "UPDATE focus_sessions SET note = ? WHERE id = ? AND user_id = ?",
        (note, session_id, user_id),
    )
    conn.commit()
    return jsonify({"success": True, "session_id": session_id, "note": note})


# ---------------------------------------------------------------------------
# Focus stats and history
# ---------------------------------------------------------------------------

@app.route("/get_study_stats")
def get_study_stats():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    # Timezone-bounded dynamic range calculation
    user_tz = _get_user_timezone(user_id)
    today_user = datetime.now(user_tz).date()
    today_start_utc, today_end_utc = _user_day_utc_bounds(user_id, today_user)
    
    seven_days_ago_user = today_user - timedelta(days=6)
    week_start_utc, _ = _user_day_utc_bounds(user_id, seven_days_ago_user)

    conn = db.get_db()
    stats = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN completed_at >= ? AND completed_at < ? THEN duration_minutes ELSE 0 END), 0) AS today_minutes,
             COALESCE(SUM(CASE WHEN completed_at >= ? THEN duration_minutes ELSE 0 END), 0) AS weekly_minutes,
             COUNT(*) AS total_sessions,
             COALESCE(SUM(duration_minutes), 0) AS total_minutes
           FROM focus_sessions
          WHERE user_id = ? AND status = 'completed'""",
        (today_start_utc, today_end_utc, week_start_utc, user_id),
    ).fetchone()
    streak = streaks.calculate_streaks(user_id)
    return jsonify({
        "today_minutes": stats["today_minutes"],
        "weekly_minutes": stats["weekly_minutes"],
        "total_sessions": stats["total_sessions"],
        "total_minutes": stats["total_minutes"],
        "current_streak": streak["current_streak"],
        "best_streak": streak["best_streak"],
    })


@app.route("/get_recent_sessions")
def get_recent_sessions():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    
    user_tz = _get_user_timezone(user_id)
    rows = db.get_db().execute(
        """SELECT fs.id, fs.duration_minutes AS minutes, fs.completed_at, fs.task_id,
                  fs.mode, fs.note,
                  COALESCE(pt.title, 'General Focus') AS task
             FROM focus_sessions fs
             LEFT JOIN planner_tasks pt ON pt.id = fs.task_id AND pt.user_id = fs.user_id
            WHERE fs.user_id = ? AND fs.status = 'completed'
            ORDER BY fs.completed_at DESC LIMIT 5""",
        (user_id,),
    ).fetchall()

    result = []
    for row in rows:
        completed_at_str = row["completed_at"]
        if completed_at_str:
            dt = _parse_utc_timestamp(completed_at_str)
            if dt:
                completed_at_str = dt.astimezone(user_tz).isoformat()
        
        result.append({
            "id": row["id"],
            "task": row["task"],
            "mode": row["mode"] or "focus",
            "minutes": row["minutes"],
            "completed_at": completed_at_str,
            "task_id": row["task_id"],
            "note": row["note"] or "",
        })

    return jsonify(result)


# ---------------------------------------------------------------------------
# Calendar page
# ---------------------------------------------------------------------------

@app.route("/calendar")
def calendar_page():
    if get_authenticated_user_id() is None:
        return _auth_redirect()
    user_id = get_authenticated_user_id()
    rows = db.get_db().execute(
        """SELECT id, title, subject, estimated_minutes
           FROM planner_tasks pt
          WHERE pt.user_id = ? AND pt.is_deleted = 0 AND pt.completed = 0
            AND pt.due_date IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM events e
                 WHERE e.planner_task_id = pt.id AND e.is_deleted = 0
            )
          ORDER BY due_date ASC, priority ASC""",
        (user_id,),
    ).fetchall()
    return render_template("calendar.html", tasks=[dict(row) for row in rows])


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@app.route("/analytics")
def analytics_page():
    if get_authenticated_user_id() is None:
        return _auth_redirect()
    return render_template("analytics.html")


@app.route("/api/analytics", methods=["GET"])
@require_auth
def get_analytics_data(user_id):
    conn = db.get_db()
    user_tz = _get_user_timezone(user_id)
    
    tasks = conn.execute(
        """SELECT id, subject, priority, due_date, estimated_minutes, completed, completed_at
           FROM planner_tasks
          WHERE user_id = ? AND is_deleted = 0""",
        (user_id,),
    ).fetchall()
    sessions = conn.execute(
        """SELECT task_id, completed_at, duration_minutes
           FROM focus_sessions
          WHERE user_id = ? AND status = 'completed'""",
        (user_id,),
    ).fetchall()

    today_user = datetime.now(user_tz).date()
    today_iso = today_user.isoformat()
    total = len(tasks)
    completed = sum(1 for task in tasks if task["completed"])
    overdue = sum(
        1 for task in tasks
        if not task["completed"] and task["due_date"] and task["due_date"] < today_iso
    )
    estimated = sum(task["estimated_minutes"] or 0 for task in tasks)
    actual = sum(item["duration_minutes"] or 0 for item in sessions)

    priority_distribution = {"high": 0, "medium": 0, "low": 0}
    subject_breakdown = {}
    actual_by_task = {}
    for item in sessions:
        if item["task_id"] is not None:
            actual_by_task[item["task_id"]] = actual_by_task.get(item["task_id"], 0) + (item["duration_minutes"] or 0)
    for task in tasks:
        priority_distribution[{1: "high", 2: "medium", 3: "low"}.get(task["priority"], "medium")] += 1
        subject = task["subject"] or "General"
        bucket = subject_breakdown.setdefault(subject, {"est_min": 0, "actual_min": 0})
        bucket["est_min"] += task["estimated_minutes"] or 0
        bucket["actual_min"] += actual_by_task.get(task["id"], 0)

    # 7-day trends bucketed according to user's localized date boundary
    trend_dates = []
    trend_completed = []
    trend_focus = []
    
    for offset in range(6, -1, -1):
        day = today_user - timedelta(days=offset)
        start_utc, end_utc = _user_day_utc_bounds(user_id, day)
        
        trend_dates.append(day.strftime("%a"))
        
        trend_completed.append(sum(
            1 for task in tasks
            if task["completed"] and task["completed_at"] and start_utc <= str(task["completed_at"]) < end_utc
        ))
        
        trend_focus.append(sum(
            item["duration_minutes"] or 0
            for item in sessions
            if item["completed_at"] and start_utc <= str(item["completed_at"]) < end_utc
        ))

    payload = {
        "summary": {
            "total_tasks": total,
            "completed_tasks": completed,
            "incomplete_tasks": total - completed,
            "completion_rate": round(completed / total * 100, 1) if total else 0.0,
            "overdue_tasks": overdue,
            "total_estimated_minutes": estimated,
            "total_actual_focus_minutes": actual,
            "streak": streaks.calculate_streaks(user_id)["current_streak"],
        },
        "priority_distribution": priority_distribution,
        "subject_breakdown": subject_breakdown,
        "trends": {"dates": trend_dates, "completed": trend_completed, "focus_minutes": trend_focus},
        "achievements": achievements.get_user_achievements(user_id),
    }
    return jsonify({"success": True, "data": payload})


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------

@app.route("/ai")
def ai_page():
    if get_authenticated_user_id() is None:
        return _auth_redirect()
    return render_template("ai.html")


def get_user_tasks_from_db(user_id):
    rows = db.get_db().execute(
        """SELECT id, title, subject, priority, due_date, estimated_minutes, completed
           FROM planner_tasks
          WHERE user_id = ? AND is_deleted = 0
          ORDER BY completed ASC,
                   COALESCE(due_date, '9999-12-31') ASC,
                   priority ASC,
                   id ASC""",
        (user_id,),
    ).fetchall()
    return [SimpleNamespace(**dict(row)) for row in rows]


@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    data = request.get_json(silent=True) or {}
    user_query = str(data.get("query", "")).strip()
    if not user_query:
        return _json_error("Query cannot be empty.", "QUERY_REQUIRED", 400)

    allowed, scope_error = validate_scope(user_query)
    if not allowed:
        return _json_error(scope_error, "OUT_OF_SCOPE", 400)

    allowed, rate_error = check_rate_limit(user_id)
    if not allowed:
        return _json_error(rate_error, "RATE_LIMITED", 429)

    try:
        response_payload = query_terra_ai(user_query, build_user_context(user_id))
        conn = db.get_db()
        safe_schedule = []
        for block in response_payload.get("proposed_schedule") or []:
            task_id = block.get("task_id")
            if task_id is not None:
                try:
                    task_id = int(task_id)
                except (TypeError, ValueError):
                    continue
                if not conn.execute(
                    "SELECT 1 FROM planner_tasks WHERE id = ? AND user_id = ? AND is_deleted = 0",
                    (task_id, user_id),
                ).fetchone():
                    continue
            safe_schedule.append(block)
        response_payload["proposed_schedule"] = safe_schedule

        if response_payload.get("proposed_task"):
            response_payload["proposed_task"] = dict(response_payload["proposed_task"])
        return jsonify(response_payload)
    except Exception:
        app.logger.exception("AI request failed")
        return _json_error("Terra could not process that request. Please try again.", "AI_ERROR", 500)


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "5000")))
