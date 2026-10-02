import re
from datetime import datetime, timezone

from db import get_db

MAX_DAILY_REQUESTS = 50
MAX_SHORT_TERM_REQUESTS = 10
SHORT_TERM_SECONDS = 60

ALLOWED_KEYWORDS = [
    "task", "planner", "calendar", "schedule", "focus", "analytics",
    "due", "overdue", "priority", "study", "workload", "behind",
    "time", "organize", "deadline", "streak", "breakdown", "plan",
]
EXTRACTION_TRIGGERS = [
    "system prompt", "system instructions", "repeat everything above", "ignore previous",
]
BLOCKED_PATTERNS = [
    r"tell me a story", r"write an essay", r"write code", r"solve my homework",
    r"who is", r"what is the capital", r"play a game", r"ignore previous instructions",
]


def check_rate_limit(user_id):
    if not isinstance(user_id, int):
        return False, "Authenticated user was not found."

    db = get_db()
    now = datetime.now(timezone.utc)
    day_start = now.strftime("%Y-%m-%d 00:00:00")

    daily = db.execute(
        "SELECT COUNT(*) AS n FROM ai_request_log WHERE user_id = ? AND requested_at >= ?",
        (user_id, day_start),
    ).fetchone()["n"]
    if daily >= MAX_DAILY_REQUESTS:
        return False, "Daily AI request limit reached. Please try again tomorrow."

    recent_rows = db.execute(
        """SELECT requested_at FROM ai_request_log
           WHERE user_id = ? ORDER BY requested_at DESC LIMIT ?""",
        (user_id, MAX_SHORT_TERM_REQUESTS),
    ).fetchall()
    short_start = now.timestamp() - SHORT_TERM_SECONDS
    recent = 0
    for row in recent_rows:
        try:
            stamp = datetime.fromisoformat(
                str(row["requested_at"]).replace("Z", "+00:00")
            ).timestamp()
            recent += stamp >= short_start
        except ValueError:
            continue

    if recent >= MAX_SHORT_TERM_REQUESTS:
        return False, "Too many AI requests in a short period. Please wait a moment and try again."

    db.execute(
        "INSERT INTO ai_request_log (user_id, requested_at) VALUES (?, ?)",
        (user_id, now.isoformat(timespec="seconds")),
    )
    db.commit()
    return True, None


def validate_scope(query):
    query_lower = query.lower()
    if any(trigger in query_lower for trigger in EXTRACTION_TRIGGERS):
        return False, "I cannot display system configuration instructions."

    if any(action in query_lower for action in (
        "write an essay", "solve this problem", "do my homework", "write code for"
    )):
        return False, "Terra helps you organize and break down assignments, but cannot write or solve schoolwork for you."

    if len(query) > 500:
        return False, "Your request is too long. Please shorten it to focus on your schedule or tasks."

    if any(re.search(pattern, query_lower) for pattern in BLOCKED_PATTERNS):
        return False, "Terra is focused on planning, organizing, and study management."

    if not any(keyword in query_lower for keyword in ALLOWED_KEYWORDS) and len(query_lower.split()) > 10:
        return False, "This query appears outside the scope of Smart Planner."
    return True, None


