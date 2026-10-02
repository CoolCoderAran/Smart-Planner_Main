from datetime import datetime

MAX_SCHEDULE_BLOCKS = 50
MAX_TITLE_LENGTH = 255


def validate_schedule(proposed_schedule, existing_tasks=None):
    """Validate and sanitize an AI-generated schedule."""
    if not proposed_schedule:
        return True, None, None
    if not isinstance(proposed_schedule, list):
        return False, None, "Invalid schedule format: Expected a list of time blocks."
    if len(proposed_schedule) > MAX_SCHEDULE_BLOCKS:
        return False, None, "Schedule contains too many proposed blocks."

    sanitized = []
    seen_slots = []

    for idx, block in enumerate(proposed_schedule):
        if not isinstance(block, dict):
            return False, None, f"Block #{idx + 1} has an invalid format."

        title = str(block.get("title", "Study Block")).strip()[:MAX_TITLE_LENGTH]
        date_str = str(block.get("date", "")).strip()
        start_str = str(block.get("start_time", "")).strip()
        end_str = str(block.get("end_time", "")).strip()

        try:
            start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return False, None, f"Block #{idx + 1} has malformed date/time formats."

        if end_dt <= start_dt:
            return False, None, f"Block #{idx + 1} ends before or at its start time."

        for old_start, old_end in seen_slots:
            if max(start_dt, old_start) < min(end_dt, old_end):
                return False, None, "Schedule collision detected between overlapping time blocks."
        seen_slots.append((start_dt, end_dt))

        sanitized.append({
            "task_id": block.get("task_id"),
            "title": title,
            "date": date_str,
            "start_time": start_str,
            "end_time": end_str,
            "duration_minutes": int((end_dt - start_dt).total_seconds() // 60),
        })

    return True, sanitized, None


