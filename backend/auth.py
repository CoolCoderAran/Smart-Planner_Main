from functools import wraps
from flask import jsonify, session


def get_authenticated_user_id():
    """Return the canonical immutable user ID from the session."""
    user_id = session.get("user_id")
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None


def get_authenticated_user():
    """Return the username for display/legacy presentation only."""
    user = session.get("username")
    return user if isinstance(user, str) and user.strip() else None


def require_auth(f):
    """Require authentication and pass the canonical user_id to the route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = get_authenticated_user_id()
        if user_id is None:
            return jsonify({"error": "Unauthorized", "code": "AUTH_REQUIRED"}), 401
        return f(user_id, *args, **kwargs)
    return decorated


def require_owned_row(conn, table, row_id, user_id, *, include_deleted=False):
    """Return an owned row using canonical user_id ownership."""
    if table not in {"planner_tasks", "events", "focus_sessions"}:
        raise ValueError("Unsupported ownership table")
    deleted_clause = "" if include_deleted else " AND is_deleted = 0"
    return conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND user_id = ?{deleted_clause}",
        (row_id, user_id),
    ).fetchone()


