from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import secrets
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables before application configuration.
load_dotenv()

# These modules define Blueprints and do not import app.py.
import achievements
import ai
import calendar_sync
import db
import focus
import planner
import streaks

from auth import get_authenticated_user_id, require_auth


BASE_DIR = Path(__file__).resolve().parent.parent

app = Flask(
    __name__,
    template_folder=BASE_DIR / "templates",
    static_folder=BASE_DIR / "static",
)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY must be configured before starting Smart Planner."
    )

app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
)

# Initialize the database and register all feature Blueprints.
db.init_db()

app.register_blueprint(planner.planner_bp)
app.register_blueprint(calendar_sync.calendar_bp)
app.register_blueprint(ai.ai_bp)
app.register_blueprint(focus.focus_bp)


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
    
    # Extract supplied CSRF token from header, form, or JSON body
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not supplied and request.is_json:
        json_data = request.get_json(silent=True) or {}
        supplied = json_data.get("csrf_token")

    if not secrets.compare_digest(str(supplied or ""), str(expected or "")):
        if request.path.startswith("/api/") or request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
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
    if request.path.startswith("/api/") or request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": "Not found.", "code": "NOT_FOUND"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    app.logger.exception("Unhandled server error")
    if request.path.startswith("/api/") or request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": "An internal server error occurred.", "code": "INTERNAL_ERROR"}), 500
    return render_template("500.html"), 500


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.exception("Unexpected error")
    if request.path.startswith("/api/") or request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": "An unexpected error occurred.", "code": "UNEXPECTED_ERROR"}), 500
    return render_template("500.html"), 500


def _json_error(message, code, status):
    return jsonify({"error": message, "code": code}), status

def _auth_redirect():
    # Only return JSON errors for data-modifying API/AJAX calls.
    # Standard page navigations (GET) must always redirect to the login page.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
        request.path.startswith("/api/")
        or request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ):
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
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
        clean_str = str(value).replace(" ", "T").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(clean_str)
    except (ValueError, TypeError):
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
    """Return UTC start and end ISO strings representing a user's local day."""
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
        data = request.get_json(silent=True) if request.is_json else request.form
        username = str(data.get("username", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        confirm_password = str(data.get("confirm_password", ""))
        terms_accepted = str(data.get("terms_accepted")).lower() in ("true", "1", "on")
        tz_name = str(data.get("timezone", "UTC")).strip()
        
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz_name = "UTC"

        errors = []
        if not username or not email or not password or not confirm_password:
            errors.append("Please fill in all fields.")
        elif len(username) > 50:
            errors.append("Username must be 50 characters or fewer.")
        elif not _email_is_plausible(email):
            errors.append("Please enter a valid email address.")
        elif password != confirm_password:
            errors.append("Passwords do not match.")
        elif not terms_accepted:
            errors.append("Please accept the Terms of Service to continue.")
        elif not _password_is_secure(password):
            errors.append("Password must contain 12+ characters, uppercase, lowercase, number, and symbol.")

        if errors:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return _json_error(errors[0], "VALIDATION_FAILED", 400)
            flash(errors[0])
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
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return _json_error("Username or email already exists.", "USER_EXISTS", 409)
            flash("Username already exists.")
            return redirect(url_for("signup"))

        session.clear()
        session.permanent = True
        session["user_id"] = cursor.lastrowid
        session["username"] = username
        session["csrf_token"] = secrets.token_urlsafe(32)

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "redirect": url_for("dashboard")})

        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_authenticated_user_id() is not None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))

        row = db.get_db().execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if not username or not password or not row or not check_password_hash(row["password_hash"], password):
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return _json_error("Invalid username or password.", "INVALID_CREDENTIALS", 401)
            flash("Invalid username or password.")
            return redirect(url_for("login"))

        session.clear()
        session.permanent = True
        session["user_id"] = row["id"]
        session["username"] = row["username"]
        session["csrf_token"] = secrets.token_urlsafe(32)

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "redirect": url_for("dashboard")})

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "redirect": url_for("login")})
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
        # Support JSON modal requests and standard Form posts
        data = request.get_json(silent=True) if request.is_json else request.form
        action = data.get("action")

        if action == "save_profile":
            display_name = str(data.get("display_name", "")).strip()[:100]
            tz_name = str(data.get("timezone", "UTC")).strip()
            
            try:
                ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                return _json_error("Invalid timezone provided.", "INVALID_TIMEZONE", 400)

            conn.execute(
                "UPDATE users SET display_name = ?, timezone = ? WHERE id = ?",
                (display_name or None, tz_name, user_id),
            )
        elif action == "save_focus_preferences":
            focus = _parse_positive_minutes(data.get("focus_mins"), 240)
            break_mins = _parse_positive_minutes(data.get("break_mins"), 60)
            if focus is None or break_mins is None:
                return _json_error("Invalid timer preference.", "INVALID_PREFERENCE", 400)
            conn.execute(
                """UPDATE users
                   SET default_focus_mins = ?, break_mins = ?
                   WHERE id = ?""",
                (focus, break_mins, user_id),
            )
        elif action == "save_ai_preferences":
            tone = str(data.get("ai_tone", "balanced")).strip().lower()
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

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": "Settings saved successfully."})

        flash("Settings saved successfully.", "success")
        return redirect(url_for("settings"))

    user = _user_row(user_id)
    return render_template(
        "settings.html",
        display_name=user["display_name"] or user["username"],
        default_focus_mins=user["default_focus_mins"] if user["default_focus_mins"] is not None else 25,
        break_mins=user["break_mins"] if user["break_mins"] is not None else 5,
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
# Calendar page
# ---------------------------------------------------------------------------

@app.route("/calendar")
def calendar_page():
    if get_authenticated_user_id() is None:
        return _auth_redirect()
    user_id = get_authenticated_user_id()
    rows = db.get_db().execute(
           """SELECT id, title, subject, due_date, due_time,
                  estimated_minutes, priority, completed
           FROM planner_tasks pt
          WHERE pt.user_id = ? AND pt.is_deleted = 0 AND pt.completed = 0
            AND pt.due_date IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM events e
                 WHERE e.planner_task_id = pt.id AND e.is_deleted = 0
            )
          ORDER BY due_date ASC, priority ASC""",
,
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

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "10000")))
