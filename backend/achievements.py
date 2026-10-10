from datetime import datetime, timezone
import db
import streaks


def unlock(conn, user_id, achievement_id):
    """Grant an achievement idempotently (INSERT OR IGNORE)."""
    conn.execute(
        """INSERT OR IGNORE INTO user_achievements
           (user_id, achievement_id, earned_at)
           VALUES (?, ?, ?)""",
        (user_id, achievement_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def check_achievements(user_id):
    """Evaluate all 50 achievement rules for user_id and unlock earned badges."""
    if not user_id:
        return

    conn = db.get_db()
    rows = conn.execute(
        "SELECT id, requirement_type, requirement_value FROM achievements"
    ).fetchall()

    try:
        for row in rows:
            achievement_id = row["id"]
            req_type = row["requirement_type"]
            req_value = row["requirement_value"]

            if req_type is None or req_value is None:
                continue

            unlocked = False

            if req_type == "sessions":
                unlocked = (
                    conn.execute(
                        "SELECT COUNT(*) FROM focus_sessions WHERE user_id = ? AND status = 'completed'",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "minutes":
                unlocked = (
                    conn.execute(
                        "SELECT COALESCE(SUM(duration_minutes), 0) FROM focus_sessions WHERE user_id = ? AND status = 'completed'",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "streak":
                unlocked = streaks.calculate_streaks(user_id)["current_streak"] >= req_value

            elif req_type == "tasks":
                unlocked = (
                    conn.execute(
                        "SELECT COUNT(*) FROM planner_tasks WHERE user_id = ? AND is_deleted = 0",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type in {"pomodoro", "deepwork", "quickburst", "examcrunch"}:
                unlocked = (
                    conn.execute(
                        "SELECT COUNT(*) FROM focus_sessions WHERE user_id = ? AND status = 'completed' AND mode = ?",
                        (user_id, req_type),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "early":
                unlocked = (
                    conn.execute(
                        """SELECT COUNT(*) FROM focus_sessions
                           WHERE user_id = ? AND status = 'completed'
                             AND CAST(strftime('%H', completed_at) AS INTEGER) < 8""",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "night":
                unlocked = (
                    conn.execute(
                        """SELECT COUNT(*) FROM focus_sessions
                           WHERE user_id = ? AND status = 'completed'
                             AND CAST(strftime('%H', completed_at) AS INTEGER) >= 22""",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "dailyminutes":
                unlocked = (
                    conn.execute(
                        """SELECT COALESCE(MAX(total), 0) FROM (
                               SELECT SUM(duration_minutes) AS total
                                 FROM focus_sessions
                                WHERE user_id = ? AND status = 'completed'
                                GROUP BY DATE(completed_at)
                           )""",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "dailysessions":
                unlocked = (
                    conn.execute(
                        """SELECT COALESCE(MAX(total), 0) FROM (
                               SELECT COUNT(*) AS total
                                 FROM focus_sessions
                                WHERE user_id = ? AND status = 'completed'
                                GROUP BY DATE(completed_at)
                           )""",
                        (user_id,),
                    ).fetchone()[0] >= req_value
                )

            elif req_type == "account_age":
                first = conn.execute(
                    "SELECT DATE(created_at) FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if first and first[0]:
                    first_dt = datetime.strptime(first[0], "%Y-%m-%d").date()
                    unlocked = (datetime.now(timezone.utc).date() - first_dt).days >= req_value

            if unlocked:
                unlock(conn, user_id, achievement_id)

        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_user_achievements(user_id):
    """Return all 50 achievement definitions with unlocked status and timestamp."""
    rows = db.get_db().execute(
        """SELECT a.id, a.title, a.description, a.icon, ua.earned_at
           FROM achievements a
           LEFT JOIN user_achievements ua 
                  ON a.id = ua.achievement_id AND ua.user_id = ?
          ORDER BY (ua.earned_at IS NULL) ASC, ua.earned_at DESC, a.id ASC""",
        (user_id,),
    ).fetchall()

    return [
        {
            "id": row["id"],
            "name": row["title"],
            "desc": row["description"],
            "icon": row["icon"],
            "earned_at": row["earned_at"],
            "unlocked": row["earned_at"] is not None,
        }
        for row in rows
    ]
