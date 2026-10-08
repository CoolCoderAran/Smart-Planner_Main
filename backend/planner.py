from datetime import datetime, timezone
from flask import Blueprint, jsonify, render_template, request

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


def validate_task_input(data):
    title = str(data.get("title", "")).strip()
    if not title:
        return None, "Task title cannot be empty or whitespace."
    if len(title) > MAX_TITLE_LENGTH:
        return None, f"Task title exceeds maximum length of {MAX_TITLE_LENGTH} characters."

    subject = str(data.get("subject", "General")).strip()[:100] or "General"

    raw_priority = data.get("priority", 2)
    priority_map = {
        "1": 1, "2": 2, "3": 3,
        1: 1, 2: 2, 3: 3,
        "High": 1, "Medium": 2, "Low": 3,
    }
    priority = priority_map.get(raw_priority)
    if priority is None:
        try:
            int_prio = int(raw_priority)
        except (TypeError, ValueError):
            return None, "Invalid priority. Expected 1 (High), 2 (Medium), or 3 (Low)."
        if int_prio not in (1, 2, 3):
            return None, "Invalid priority. Expected 1 (High), 2 (Medium), or 3 (Low)."
        priority = int_prio

    raw_minutes = data.get("estimated_minutes", 30)
    try:
        estimated_minutes = int(raw_minutes)
    except (ValueError, TypeError):
        return None, "Estimated duration must be a valid integer."
    if estimated_minutes <= 0:
        return None, "Estimated minutes must be greater than zero."
    if estimated_minutes > 1440:
        return None, "Estimated minutes must be at most 1440 (24 hours)."

    raw_date = str(data.get("due_date") or "").strip()
    if not raw_date or raw_date.lower() in ("none", "null"):
        due_date = None
    elif raw_date.lower() == "today":
        due_date = _utc_today()
    else:
        try:
            due_date = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None, "Invalid due date format. Expected YYYY-MM-DD."

    raw_time = str(data.get("due_time") or "").strip()
    if not raw_time or raw_time.lower() in ("none", "null"):
        due_time = None
    else:
        try:
            due_time = datetime.strptime(raw_time, "%H:%M").strftime("%H:%M")
        except ValueError:
            return None, "Invalid due time format. Expected HH:MM in 24-hour time."

    return {
        "title": title,
        "subject": subject,
        "priority": priority,
        "estimated_minutes": estimated_minutes,
        "due_date": due_date,
        "due_time": due_time,
    }, None


def _task_response(conn, task_id, user_id):
    row = conn.execute(
        "SELECT * FROM planner_tasks WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["subtasks"] = [
        dict(item) for item in conn.execute(
            """SELECT * FROM planner_tasks
               WHERE parent_id = ? AND user_id = ? AND is_deleted = 0
               ORDER BY completed ASC, id ASC""",
            (task_id, user_id),
        ).fetchall()
    ]
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
        item["subtasks"] = [
            dict(st) for st in conn.execute(
                """SELECT * FROM planner_tasks
                   WHERE user_id = ? AND is_deleted = 0 AND parent_id = ?
                   ORDER BY completed ASC, id ASC""",
                (user_id, task["id"]),
            ).fetchall()
        ]
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
    payload = request.get_json(silent=True) or request.form.to_dict()
    validated, error = validate_task_input(payload)
    if error:
        return api_error(error, "VALIDATION_ERROR", 400)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO planner_tasks
           (user_id, title, subject, due_date, due_time, estimated_minutes, priority, completed, is_deleted)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
        (user_id, validated["title"], validated["subject"], validated["due_date"],
         validated["due_time"], validated["estimated_minutes"], validated["priority"]),
    )
    task_id = cursor.lastrowid
    conn.commit()

    achievements.check_achievements(user_id)
    return jsonify({"success": True, "task": _task_response(conn, task_id, user_id)}), 201

@planner_bp.route("/update/<int:task_id>", methods=["POST"])
@require_auth
def update_task(user_id, task_id):
    payload = request.get_json(silent=True) or request.form.to_dict()

    conn = get_db()

    row = require_owned_row(
        conn,
        "planner_tasks",
        task_id,
        user_id,
    )

    if not row or row["is_deleted"]:
        return api_error(
            "Task not found.",
            "TASK_NOT_FOUND",
            404,
        )

    validated, error = validate_task_input(payload)

    if error:
        return api_error(
            error,
            "VALIDATION_ERROR",
            400,
        )

    conn.execute(
        """
        UPDATE planner_tasks
        SET title = ?,
            subject = ?,
            due_date = ?,
            due_time = ?,
            estimated_minutes = ?,
            priority = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND user_id = ?
          AND is_deleted = 0
        """,
        (
            validated["title"],
            validated["subject"],
            validated["due_date"],
            validated["due_time"],
            validated["estimated_minutes"],
            validated["priority"],
            task_id,
            user_id,
        ),
    )

    conn.commit()

    return jsonify({
        "success": True,
        "task": _task_response(
            conn,
            task_id,
            user_id,
        ),
    })

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
    conn.commit()

    achievements.check_achievements(user_id)
    return jsonify({"success": True, "task": _task_response(conn, task_id, user_id)})


@planner_bp.route("/delete/<int:task_id>", methods=["POST", "DELETE"])
@require_auth
def delete_task(user_id, task_id):
    conn = get_db()
    row = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not row:
        return api_error("Task not found.", "TASK_NOT_FOUND", 404)

    conn.execute(
        "UPDATE planner_tasks SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    return jsonify({"success": True, "message": "Task deleted successfully."})

@planner_bp.route("/subtask/add", methods=["POST"])
@require_auth
def add_subtask(user_id):
    payload = request.get_json(silent=True) or request.form.to_dict()

    parent_id = payload.get("parent_id")

    try:
        parent_id = int(parent_id)
    except (TypeError, ValueError):
        return api_error(
            "A valid parent task ID is required.",
            "INVALID_PARENT_ID",
            400,
        )

    title = str(payload.get("title", "")).strip()

    if not title:
        return api_error(
            "Subtask title is required.",
            "TITLE_REQUIRED",
            400,
        )

    if len(title) > 255:
        return api_error(
            "Subtask title is too long.",
            "TITLE_TOO_LONG",
            400,
        )

    conn = get_db()

    parent = conn.execute(
        """
        SELECT id
        FROM planner_tasks
        WHERE id = ?
          AND user_id = ?
          AND is_deleted = 0
        """,
        (parent_id, user_id),
    ).fetchone()

    if not parent:
        return api_error(
            "Parent task not found.",
            "PARENT_TASK_NOT_FOUND",
            404,
        )

    cursor = conn.execute(
        """
        INSERT INTO planner_tasks (
            user_id,
            parent_id,
            title,
            subject,
            due_date,
            due_time,
            estimated_minutes,
            priority,
            completed,
            is_deleted
        )
        SELECT
            user_id,
            ?,
            ?,
            subject,
            due_date,
            due_time,
            estimated_minutes,
            priority,
            0,
            0
        FROM planner_tasks
        WHERE id = ?
          AND user_id = ?
          AND is_deleted = 0
        """,
        (
            parent_id,
            title,
            parent_id,
            user_id,
        ),
    )

    conn.commit()

    subtask_id = cursor.lastrowid

    return jsonify({
        "success": True,
        "task": _task_response(
            conn,
            subtask_id,
            user_id,
        ),
    }), 201

@planner_bp.route("/batch", methods=["POST"])
@require_auth
def batch_tasks(user_id):
    payload = request.get_json(silent=True) or {}

    action = str(payload.get("action", "")).strip().lower()
    task_ids = payload.get("task_ids", [])

    if action not in {
        "complete",
        "delete",
        "shift_today",
    }:
        return api_error(
            "Unsupported batch action.",
            "INVALID_BATCH_ACTION",
            400,
        )

    if not isinstance(task_ids, list) or not task_ids:
        return api_error(
            "At least one task ID is required.",
            "TASK_IDS_REQUIRED",
            400,
        )

    try:
        task_ids = list({
            int(task_id)
            for task_id in task_ids
        })
    except (TypeError, ValueError):
        return api_error(
            "Task IDs must be integers.",
            "INVALID_TASK_IDS",
            400,
        )

    conn = get_db()

    placeholders = ",".join("?" for _ in task_ids)

    owned_rows = conn.execute(
        f"""
        SELECT id
        FROM planner_tasks
        WHERE user_id = ?
          AND is_deleted = 0
          AND id IN ({placeholders})
        """,
        [user_id, *task_ids],
    ).fetchall()

    owned_ids = {row["id"] for row in owned_rows}

    unauthorized_ids = set(task_ids) - owned_ids

    if unauthorized_ids:
        return api_error(
            "One or more tasks were not found.",
            "TASK_NOT_FOUND",
            404,
        )

    if action == "complete":
        conn.execute(
            f"""
            UPDATE planner_tasks
            SET completed = 1,
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND is_deleted = 0
              AND id IN ({placeholders})
            """,
            [user_id, *task_ids],
        )

    elif action == "delete":
        conn.execute(
            f"""
            UPDATE planner_tasks
            SET is_deleted = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND is_deleted = 0
              AND id IN ({placeholders})
            """,
            [user_id, *task_ids],
        )

    elif action == "shift_today":
        conn.execute(
            f"""
            UPDATE planner_tasks
            SET due_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND is_deleted = 0
              AND id IN ({placeholders})
            """,
            [_utc_today(), user_id, *task_ids],
        )

    conn.commit()

    achievements.check_achievements(user_id)

    return jsonify({
        "success": True,
        "action": action,
        "updated": len(task_ids),
    })
    
