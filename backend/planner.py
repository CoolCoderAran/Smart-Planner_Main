from datetime import date, datetime, timezone
from flask import Blueprint, jsonify, render_template, request

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
        # Unknown priority value — reject rather than silently normalise to
        # an unexpected value.  Numeric strings outside {1,2,3} also land
        # here so the caller gets a clear error.
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

    # due_date: empty / missing / "none" / "null" → NULL (unscheduled).
    # "today" → today's UTC date.  Otherwise validate YYYY-MM-DD.
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

    # due_time: empty / missing → NULL.  Otherwise validate HH:MM.
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


@planner_bp.route("", methods=["GET"])
@require_auth
def planner_index(user_id):
    conn = get_db()
    # COALESCE ensures unscheduled tasks (NULL due_date) sort after all dated
    # tasks rather than bubbling to the top (SQLite NULLs sort first in ASC).
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
    return render_template(
        "planner.html",
        tasks=tasks,
        username=username_row["username"],
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

    # Check achievements whenever a task is created (e.g. "first_task").
    try:
        import achievements as ach
        ach.check_achievements(user_id)
    except Exception:
        pass  # Achievement failure must never block the primary operation.

    return jsonify({
        "message": "Task created successfully",
        "task": _task_response(conn, task_id, user_id),
        "id": task_id,
    }), 201


@planner_bp.route("/subtask/add", methods=["POST"])
@require_auth
def add_subtask(user_id):
    payload = request.get_json(silent=True) or request.form.to_dict()
    parent_id = payload.get("parent_id")
    try:
        parent_id = int(parent_id)
    except (TypeError, ValueError):
        return api_error("Missing or invalid parent task ID.", "PARENT_ID_REQUIRED", 400)

    conn = get_db()
    parent = require_owned_row(conn, "planner_tasks", parent_id, user_id)
    if not parent:
        return api_error("Parent task not found or access denied.", "TASK_NOT_FOUND", 404)

    validated, error = validate_task_input(payload)
    if error:
        return api_error(error, "VALIDATION_ERROR", 400)

    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO planner_tasks
           (user_id, title, subject, due_date, due_time, estimated_minutes, priority, completed, is_deleted, parent_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
        (user_id, validated["title"], parent["subject"], parent["due_date"], parent["due_time"],
         validated["estimated_minutes"], validated["priority"], parent_id),
    )
    subtask_id = cursor.lastrowid
    conn.commit()
    return jsonify({
        "message": "Subtask added successfully",
        "subtask": _task_response(conn, subtask_id, user_id),
        "id": subtask_id,
    }), 201


@planner_bp.route("/update/<int:task_id>", methods=["POST", "PUT"])
@require_auth
def update_task(user_id, task_id):
    payload = request.get_json(silent=True) or request.form.to_dict()
    conn = get_db()
    if not require_owned_row(conn, "planner_tasks", task_id, user_id):
        return api_error("Task not found or permission denied.", "TASK_NOT_FOUND", 404)

    validated, error = validate_task_input(payload)
    if error:
        return api_error(error, "VALIDATION_ERROR", 400)

    conn.execute(
        """UPDATE planner_tasks
           SET title = ?, subject = ?, due_date = ?, due_time = ?,
               estimated_minutes = ?, priority = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND user_id = ?""",
        (validated["title"], validated["subject"], validated["due_date"], validated["due_time"],
         validated["estimated_minutes"], validated["priority"], task_id, user_id),
    )
    conn.commit()
    return jsonify({"message": "Task updated successfully", "task": _task_response(conn, task_id, user_id)})


@planner_bp.route("/toggle/<int:task_id>", methods=["POST"])
@require_auth
def toggle_task(user_id, task_id):
    conn = get_db()
    task = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not task:
        return api_error("Task not found or permission denied.", "TASK_NOT_FOUND", 404)

    new_status = 0 if task["completed"] else 1
    completed_at = "CURRENT_TIMESTAMP" if new_status else "NULL"
    conn.execute(
        f"UPDATE planner_tasks SET completed = ?, completed_at = {completed_at}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (new_status, task_id, user_id),
    )
    if task["parent_id"] is None:
        conn.execute(
            f"""UPDATE planner_tasks SET completed = ?, completed_at = {completed_at}, updated_at = CURRENT_TIMESTAMP
               WHERE parent_id = ? AND user_id = ? AND is_deleted = 0""",
            (new_status, task_id, user_id),
        )
    conn.commit()

    if new_status == 1:
        try:
            import achievements as ach
            ach.check_achievements(user_id)
        except Exception:
            pass

    return jsonify({"message": "Task toggled successfully", "task_id": task_id, "completed": new_status})


@planner_bp.route("/undo/<int:task_id>", methods=["POST"])
@require_auth
def undo_completion(user_id, task_id):
    conn = get_db()
    task = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not task:
        return api_error("Task not found or permission denied.", "TASK_NOT_FOUND", 404)
    conn.execute(
        "UPDATE planner_tasks SET completed = 0, completed_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    )
    conn.commit()
    return jsonify({"message": "Task restored successfully", "task_id": task_id, "completed": 0})


@planner_bp.route("/complete/<int:task_id>", methods=["POST"])
@require_auth
def complete_task(user_id, task_id):
    conn = get_db()
    task = require_owned_row(conn, "planner_tasks", task_id, user_id)
    if not task:
        return api_error("Task not found or permission denied.", "TASK_NOT_FOUND", 404)
    conn.execute(
        "UPDATE planner_tasks SET completed = 1, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    )
    conn.commit()

    try:
        import achievements as ach
        ach.check_achievements(user_id)
    except Exception:
        pass

    return jsonify({"message": "Task completed.", "task_id": task_id, "completed": 1})


@planner_bp.route("/delete/<int:task_id>", methods=["DELETE", "POST"])
@require_auth
def delete_task(user_id, task_id):
    conn = get_db()
    if not require_owned_row(conn, "planner_tasks", task_id, user_id):
        return api_error("Task not found or permission denied.", "TASK_NOT_FOUND", 404)
    conn.execute(
        "UPDATE planner_tasks SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    )
    conn.execute(
        "UPDATE planner_tasks SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE parent_id = ? AND user_id = ?",
        (task_id, user_id),
    )
    conn.commit()
    return jsonify({"message": "Task soft-deleted successfully", "task_id": task_id})


@planner_bp.route("/batch", methods=["POST"])
@require_auth
def batch_action(user_id):
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    task_ids = payload.get("task_ids", [])

    if not isinstance(task_ids, list):
        return api_error("task_ids must be a list.", "INVALID_BATCH", 400)

    # Empty selection is not an error; it is a no-op.
    if not task_ids:
        return jsonify({"message": "No tasks selected.", "affected_ids": []})

    if action not in {"complete", "shift_today", "delete"}:
        return api_error("Invalid batch action.", "INVALID_BATCH", 400)

    if len(task_ids) > MAX_BATCH_TASKS:
        return api_error(f"Batch size cannot exceed {MAX_BATCH_TASKS} tasks.", "BATCH_TOO_LARGE", 400)

    try:
        task_ids = list(dict.fromkeys(int(tid) for tid in task_ids))
    except (TypeError, ValueError):
        return api_error("Task IDs must be integers.", "INVALID_TASK_IDS", 400)

    conn = get_db()
    placeholders = ",".join("?" for _ in task_ids)
    owned = conn.execute(
        f"""SELECT id FROM planner_tasks
            WHERE user_id = ? AND is_deleted = 0 AND id IN ({placeholders})""",
        [user_id, *task_ids],
    ).fetchall()
    valid_ids = [row["id"] for row in owned]

    # If any requested IDs are not owned by this user, reject the entire batch
    # rather than silently processing a subset.
    if len(valid_ids) != len(task_ids):
        return api_error(
            "One or more task IDs are invalid or not owned by this user.",
            "TASK_OWNERSHIP_ERROR",
            403,
        )

    valid_placeholders = ",".join("?" for _ in valid_ids)
    try:
        if action == "complete":
            conn.execute(
                f"UPDATE planner_tasks SET completed = 1, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id IN ({valid_placeholders})",
                valid_ids,
            )
        elif action == "shift_today":
            conn.execute(
                f"UPDATE planner_tasks SET due_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({valid_placeholders})",
                [_utc_today(), *valid_ids],
            )
        else:  # delete
            conn.execute(
                f"""UPDATE planner_tasks SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND (id IN ({valid_placeholders}) OR parent_id IN ({valid_placeholders}))""",
                [user_id, *valid_ids, *valid_ids],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if action == "complete":
        try:
            import achievements as ach
            ach.check_achievements(user_id)
        except Exception:
            pass

    return jsonify({
        "message": f"Batch action '{action}' executed successfully.",
        "affected_ids": valid_ids,
    })


@planner_bp.route("/accept-proposal", methods=["POST"])
@require_auth
def accept_proposal(user_id):
    payload = request.get_json(silent=True) or {}
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

    try:
        import achievements as ach
        ach.check_achievements(user_id)
    except Exception:
        pass

    return jsonify({
        "success": True,
        "message": "Proposed task added to Planner.",
        "task": _task_response(conn, task_id, user_id),
    }), 201
