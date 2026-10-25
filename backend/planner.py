from datetime import datetime, timedelta, timezone
import re
from flask import Blueprint, jsonify, render_template, request, redirect

import achievements
from auth import require_auth, require_owned_row
from db import get_db

planner_bp = Blueprint("planner", __name__, url_prefix="/planner")
MAX_BATCH_TASKS = 100
MAX_TITLE_LENGTH = 255


def _utc_today():
    return datetime.now(timezone.utc).date().isoformat()


def api_error(message, code, status):
    return jsonify({"error": message, "code": code}), status


def parse_natural_language_input(title_str):
    """
    Parses Todoist-style shortcuts inside task titles:
    - Priority: p1 (High), p2 (Medium), p3 (Low)
    - Relative dates: 'today', 'tomorrow'
    """
    priority = 2
    due_date = None
    due_time = "23:59"

    prio_match = re.search(r'\b[pP]([1-3])\b', title_str)
    if prio_match:
        priority = int(prio_match.group(1))
        title_str = re.sub(r'\b[pP][1-3]\b', '', title_str)

    lower_title = title_str.lower()
    today_dt = datetime.now(timezone.utc).date()
    if "today" in lower_title:
        due_date = today_dt.isoformat()
        title_str = re.sub(r'\btoday\b', '', title_str, flags=re.IGNORECASE)
    elif "tomorrow" in lower_title:
        due_date = (today_dt + timedelta(days=1)).isoformat()
        title_str = re.sub(r'\btomorrow\b', '', title_str, flags=re.IGNORECASE)

    clean_title = re.sub(r'\s+', ' ', title_str).strip()
    return clean_title, priority, due_date, due_time


def validate_task_input(data):
    raw_title = str(data.get("title", "")).strip()
    if not raw_title:
        return None, "Task title cannot be empty or whitespace."

    clean_title, parsed_prio, parsed_date, parsed_time = parse_natural_language_input(raw_title)

    if len(clean_title) > MAX_TITLE_LENGTH:
        return None, f"Task title exceeds maximum length of {MAX_TITLE_LENGTH} characters."

    subject = str(data.get("subject", "General")).strip()[:100] or "General"

    raw_priority = data.get("priority", parsed_prio)
    priority_map = {"1": 1, "2": 2, "3": 3, 1: 1, 2: 2, 3: 3}
    priority = priority_map.get(raw_priority, parsed_prio)

    raw_minutes = data.get("estimated_minutes", 30)
    try:
        estimated_minutes = int(raw_minutes)
    except (ValueError, TypeError):
        return None, "Estimated duration must be a valid integer."
    if estimated_minutes <= 0 or estimated_minutes > 1440:
        return None, "Estimated minutes must be between 1 and 1440."

    raw_date = str(data.get("due_date") or parsed_date or "").strip()
    if not raw_date:
        due_date = None
    else:
        try:
            due_date = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None, "Invalid due date format. Expected YYYY-MM-DD."

    raw_time = str(data.get("due_time") or parsed_time or "").strip()
    if not raw_time:
        due_time = None
    else:
        try:
            due_time = datetime.strptime(raw_time, "%H:%M").strftime("%H:%M")
        except ValueError:
            return None, "Invalid due time format. Expected HH:MM."

    return {
        "title": clean_title,
        "subject": subject,
        "priority": priority,
        "estimated_minutes": estimated_minutes,
        "due_date": due_date,
        "due_time": due_time,
        "notes": str(data.get("notes", "") or "").strip()[:5000],
    }, None


def _get_subtasks_recursive(conn, parent_id, user_id):
    rows = conn.execute(
        """SELECT * FROM planner_tasks
           WHERE parent_id = ? AND user_id = ? AND is_deleted = 0
           ORDER BY completed ASC, priority ASC, id ASC""",
        (parent_id, user_id),
    ).fetchall()
    
    subtasks = []
    for row in rows:
        st = dict(row)
        st["subtasks"] = _get_subtasks_recursive(conn, st["id"], user_id)
        subtasks.append(st)
    return subtasks


def _task_response(conn, task_id, user_id):
    row = conn.execute(
        "SELECT * FROM planner_tasks WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["subtasks"] = _get_subtasks_recursive(conn, task_id, user_id)
    return result


@planner_bp.route("", methods=["GET"], strict_slashes=False)
@require_auth
def planner_index(user_id):
    conn = get_db()
    raw_tasks = conn.execute(
        """SELECT * FROM planner_tasks
           WHERE user_id = ? AND is_deleted = 0 AND parent_id IS NULL
           ORDER BY completed ASC,
                    COALESCE(due_date, '9999-12-31') ASC,
                    priority ASC,
                    COALESCE(due_time, '23:59') ASC,
                    id ASC""",
        (user_id,),
    ).fetchall()

    tasks = []
    today = _utc_today()
    for task in raw_tasks:
        item = dict(task)
        item["is_overdue"] = bool(
            not item["completed"]
            and item.get("due_date")
            and item["due_date"] < today
        )
        item["subtasks"] = _get_subtasks_recursive(conn, task["id"], user_id)
        tasks.append(item)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("json") == "1":
        return jsonify({"tasks": tasks})

    username_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    username = username_row["username"] if username_row else ""
    return render_template(
        "planner.html",
        tasks=tasks,
        username=username,
        today_date=today,
    )


@planner_bp.route("/add", methods=["POST"])
@require_auth
def add_task(user_id):
    payload = request.get_json(silent=True) or {}
    validated, error = validate_task_input(payload)
    if error:
        return api_error(error, "VALIDATION_ERROR", 400)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO planner_tasks
           (user_id, title, subject, due_date, due_time, estimated_minutes, priority, notes, completed, is_deleted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)""",
        (user_id, validated["title"], validated["subject"], validated["due_date"],
         validated["due_time"], validated["estimated_minutes"], validated["priority"], validated["notes"]),
    )
    task_id = cursor.lastrowid
    conn.commit()

    achievements.check_achievements(user_id)
    return jsonify({"success": True, "task": _task_response(conn, task_id, user_id)}), 201


@planner_bp.route("/update/<int:task_id>", methods=["POST"])
@require_auth
def update_task(user_id, task_id):
    payload = request.get_json(silent=True) or {}
    conn = get_db()

    row = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not row or row["is_deleted"]:
        return api_error("Task not found.", "TASK_NOT_FOUND", 404)

    validated, error = validate_task_input(payload)
    if error:
        return api_error(error, "VALIDATION_ERROR", 400)

    conn.execute(
        """UPDATE planner_tasks
           SET title = ?, subject = ?, due_date = ?, due_time = ?,
               estimated_minutes = ?, priority = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND user_id = ? AND is_deleted = 0""",
        (validated["title"], validated["subject"], validated["due_date"],
         validated["due_time"], validated["estimated_minutes"], validated["priority"],
         validated["notes"], task_id, user_id),
    )
    conn.commit()
    return jsonify({"success": True, "task": _task_response(conn, task_id, user_id)})


@planner_bp.route("/toggle/<int:task_id>", methods=["POST"])
@require_auth
def toggle_task(user_id, task_id):
    conn = get_db()
    row = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not row:
        return api_error("Task not found.", "TASK_NOT_FOUND", 404)

    new_status = 0 if row["completed"] else 1
    completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if new_status else None

    conn.execute(
        "UPDATE planner_tasks SET completed = ?, completed_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_status, completed_at, task_id),
    )
    
    conn.execute(
        "UPDATE planner_tasks SET completed = ?, completed_at = ? WHERE parent_id = ?",
        (new_status, completed_at, task_id),
    )
    
    conn.commit()
    achievements.check_achievements(user_id)

    # Return JSON for AJAX requests
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "task": _task_response(conn, task_id, user_id)})

    # Redirect to referrer page or explicit dashboard route for standard HTML form posts
    return redirect(request.referrer or "/dashboard")

@planner_bp.route("/delete/<int:task_id>", methods=["POST", "DELETE"])
@require_auth
def delete_task(user_id, task_id):
    conn = get_db()
    row = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not row:
        return api_error("Task not found.", "TASK_NOT_FOUND", 404)

    conn.execute(
        "UPDATE planner_tasks SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ? OR parent_id = ?",
        (task_id, task_id),
    )
    conn.commit()
    return jsonify({"success": True, "message": "Task deleted successfully."})


@planner_bp.route("/subtask/add", methods=["POST"])
@require_auth
def add_subtask(user_id):
    payload = request.get_json(silent=True) or {}
    parent_id = payload.get("parent_id")

    try:
        parent_id = int(parent_id)
    except (TypeError, ValueError):
        return api_error("A valid parent task ID is required.", "INVALID_PARENT_ID", 400)

    title = str(payload.get("title", "")).strip()
    if not title:
        return api_error("Subtask title is required.", "TITLE_REQUIRED", 400)

    conn = get_db()
    parent = conn.execute(
        "SELECT id, subject, due_date, due_time, priority FROM planner_tasks WHERE id = ? AND user_id = ? AND is_deleted = 0",
        (parent_id, user_id),
    ).fetchone()

    if not parent:
        return api_error("Parent task not found.", "PARENT_TASK_NOT_FOUND", 404)

    clean_title, priority, due_date, due_time = parse_natural_language_input(title)

    cursor = conn.execute(
        """INSERT INTO planner_tasks (
            user_id, parent_id, title, subject, due_date, due_time,
            estimated_minutes, priority, completed, is_deleted
        ) VALUES (?, ?, ?, ?, ?, ?, 15, ?, 0, 0)""",
        (user_id, parent_id, clean_title, parent["subject"], 
         due_date or parent["due_date"], due_time or parent["due_time"], priority),
    )
    conn.commit()
    return jsonify({"success": True, "task": _task_response(conn, cursor.lastrowid, user_id)}), 201


@planner_bp.route("/batch", methods=["POST"])
@require_auth
def batch_tasks(user_id):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).strip().lower()
    task_ids = payload.get("task_ids", [])

    if action not in {"complete", "delete", "shift_today"}:
        return api_error("Unsupported batch action.", "INVALID_BATCH_ACTION", 400)

    if not isinstance(task_ids, list) or not task_ids:
        return api_error("At least one task ID is required.", "TASK_IDS_REQUIRED", 400)

    try:
        task_ids = list({int(tid) for tid in task_ids})
    except (TypeError, ValueError):
        return api_error("Task IDs must be integers.", "INVALID_TASK_IDS", 400)

    conn = get_db()
    placeholders = ",".join("?" for _ in task_ids)

    owned_rows = conn.execute(
        f"SELECT id FROM planner_tasks WHERE user_id = ? AND is_deleted = 0 AND id IN ({placeholders})",
        [user_id, *task_ids],
    ).fetchall()

    if len(owned_rows) != len(task_ids):
        return api_error("One or more tasks were not found.", "TASK_NOT_FOUND", 404)

    if action == "complete":
        conn.execute(
            f"UPDATE planner_tasks SET completed = 1, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *task_ids],
        )
    elif action == "delete":
        conn.execute(
            f"UPDATE planner_tasks SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *task_ids],
        )
    elif action == "shift_today":
        conn.execute(
            f"UPDATE planner_tasks SET due_date = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND id IN ({placeholders})",
            [_utc_today(), user_id, *task_ids],
        )

    conn.commit()
    achievements.check_achievements(user_id)
    return jsonify({"success": True, "action": action, "updated": len(task_ids)})
