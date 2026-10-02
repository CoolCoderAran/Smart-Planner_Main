from datetime import date, datetime, timedelta, timezone

from db import get_db


def calculate_streaks(user_id, today=None):
    """Calculate streaks from authoritative focus_sessions for one user."""
    rows = get_db().execute(
        """SELECT DISTINCT DATE(completed_at) AS study_date
           FROM focus_sessions
          WHERE user_id = ? AND completed_at IS NOT NULL
          ORDER BY study_date DESC""",
        (user_id,),
    ).fetchall()

    if not rows:
        return {"current_streak": 0, "best_streak": 0}

    dates = [datetime.strptime(row["study_date"], "%Y-%m-%d").date() for row in rows]
    today = today or datetime.now(timezone.utc).date()

    current = 0
    if dates[0] in (today, today - timedelta(days=1)):
        current = 1
        for previous in dates[1:]:
            if dates[current - 1] - previous == timedelta(days=1):
                current += 1
            else:
                break

    best = run = 1
    for newer, older in zip(dates, dates[1:]):
        if newer - older == timedelta(days=1):
            run += 1
        else:
            best = max(best, run)
            run = 1
    best = max(best, run)
    return {"current_streak": current, "best_streak": max(best, current)}


