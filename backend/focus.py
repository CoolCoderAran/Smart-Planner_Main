@app.route("/study")
@app.route("/focus")
def study():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _auth_redirect()
    
    user = _user_row(user_id)
    if user is None:
        session.clear()
        return _auth_redirect()

    rows = db.get_db().execute(
        """SELECT id, title, subject
           FROM planner_tasks
          WHERE user_id = ? AND is_deleted = 0 AND completed = 0
          ORDER BY COALESCE(due_date, '9999-12-31') ASC, priority ASC, id ASC""",
        (user_id,),
    ).fetchall()

    return render_template(
        "study.html",
        planner_tasks=[dict(row) for row in rows],
        selected_task_id=request.args.get("task_id", type=int),
        default_focus_mins=user["default_focus_mins"] if user["default_focus_mins"] is not None else 25,
        break_mins=user["break_mins"] if user["break_mins"] is not None else 5,
    )


@app.route("/focus/start", methods=["POST"])
def start_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
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

    now_iso = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = conn.execute(
        """INSERT INTO focus_sessions
           (user_id, task_id, started_at, completed_at, duration_minutes, status, mode)
           VALUES (?, ?, ?, NULL, 0, 'active', ?)""",
        (user_id, task_id, now_iso, mode),
    )
    conn.commit()
    return jsonify({"success": True, "session_id": cursor.lastrowid, "started_at": now_iso})


@app.route("/focus/pause", methods=["POST"])
def pause_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)
    
    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
    try:
        session_id = int(data.get("session_id"))
    except (TypeError, ValueError):
        return _json_error("A valid focus session ID is required.", "INVALID_SESSION_ID", 400)

    conn = db.get_db()
    now_iso = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = conn.execute(
        """UPDATE focus_sessions
              SET paused_at = ?
            WHERE id = ? AND user_id = ? AND status = 'active' AND paused_at IS NULL""",
        (now_iso, session_id, user_id),
    ).rowcount
    conn.commit()
    if not changed:
        return _json_error("Focus session is not active or is already paused.", "SESSION_NOT_PAUSABLE", 409)
    return jsonify({"success": True, "session_id": session_id, "paused_at": now_iso})


@app.route("/focus/complete", methods=["POST"])
def complete_focus():
    user_id = get_authenticated_user_id()
    if user_id is None:
        return _json_error("Unauthorized access.", "AUTH_REQUIRED", 401)

    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
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
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    updated = conn.execute(
        """UPDATE focus_sessions
              SET completed_at = ?, duration_minutes = ?, status = 'completed', paused_at = NULL,
                  note = COALESCE(NULLIF(?, ''), note)
            WHERE id = ? AND user_id = ? AND status = 'active'""",
        (now_iso, elapsed_minutes, note, session_id, user_id),
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
    
    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
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

@app.route("/focus/resume", methods=["POST"])
def resume_focus():
    user_id = get_authenticated_user_id()

    if user_id is None:
        return _json_error(
            "Unauthorized access.",
            "AUTH_REQUIRED",
            401,
        )

    data = (
        request.get_json(silent=True)
        if request.is_json
        else request.form
    ) or {}

    try:
        session_id = int(data.get("session_id"))
    except (TypeError, ValueError):
        return _json_error(
            "A valid focus session ID is required.",
            "INVALID_SESSION_ID",
            400,
        )

    conn = db.get_db()

    row = conn.execute(
        """
        SELECT id, paused_at
        FROM focus_sessions
        WHERE id = ?
          AND user_id = ?
          AND status = 'active'
        """,
        (session_id, user_id),
    ).fetchone()

    if not row:
        return _json_error(
            "Focus session not found or is not active.",
            "SESSION_NOT_FOUND",
            404,
        )

    if not row["paused_at"]:
        return _json_error(
            "Focus session is not paused.",
            "SESSION_NOT_PAUSED",
            409,
        )

    paused_since = _parse_utc_timestamp(row["paused_at"])

    if paused_since is None:
        return _json_error(
            "Stored pause time is invalid.",
            "SESSION_TIME_INVALID",
            500,
        )

    now = _utc_now()

    paused_seconds = max(
        0,
        int((now - paused_since).total_seconds()),
    )

    conn.execute(
        """
        UPDATE focus_sessions
        SET paused_at = NULL,
            total_paused_seconds =
                COALESCE(total_paused_seconds, 0) + ?
        WHERE id = ?
          AND user_id = ?
          AND status = 'active'
        """,
        (
            paused_seconds,
            session_id,
            user_id,
        ),
    )

    conn.commit()

    return jsonify({
        "success": True,
        "session_id": session_id,
        "resumed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "paused_seconds": paused_seconds,
    })@app.route("/focus/resume", methods=["POST"])
def resume_focus():
    user_id = get_authenticated_user_id()

    if user_id is None:
        return _json_error(
            "Unauthorized access.",
            "AUTH_REQUIRED",
            401,
        )

    data = (
        request.get_json(silent=True)
        if request.is_json
        else request.form
    ) or {}

    try:
        session_id = int(data.get("session_id"))
    except (TypeError, ValueError):
        return _json_error(
            "A valid focus session ID is required.",
            "INVALID_SESSION_ID",
            400,
        )

    conn = db.get_db()

    row = conn.execute(
        """
        SELECT id, paused_at
        FROM focus_sessions
        WHERE id = ?
          AND user_id = ?
          AND status = 'active'
        """,
        (session_id, user_id),
    ).fetchone()

    if not row:
        return _json_error(
            "Focus session not found or is not active.",
            "SESSION_NOT_FOUND",
            404,
        )

    if not row["paused_at"]:
        return _json_error(
            "Focus session is not paused.",
            "SESSION_NOT_PAUSED",
            409,
        )

    paused_since = _parse_utc_timestamp(row["paused_at"])

    if paused_since is None:
        return _json_error(
            "Stored pause time is invalid.",
            "SESSION_TIME_INVALID",
            500,
        )

    now = _utc_now()

    paused_seconds = max(
        0,
        int((now - paused_since).total_seconds()),
    )

    conn.execute(
        """
        UPDATE focus_sessions
        SET paused_at = NULL,
            total_paused_seconds =
                COALESCE(total_paused_seconds, 0) + ?
        WHERE id = ?
          AND user_id = ?
          AND status = 'active'
        """,
        (
            paused_seconds,
            session_id,
            user_id,
        ),
    )

    conn.commit()

    return jsonify({
        "success": True,
        "session_id": session_id,
        "resumed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "paused_seconds": paused_seconds,
    })
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
    
    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
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

    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
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
