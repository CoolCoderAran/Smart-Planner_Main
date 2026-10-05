from functools import wraps
from flask import jsonify, redirect, request, session, url_for


def get_authenticated_user_id():
    return session.get("user_id")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = get_authenticated_user_id()
        if user_id is None:
            # Return JSON error for POST/PUT/PATCH/DELETE or API/AJAX calls
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} or (
                request.path.startswith("/api/")
                or request.is_json
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            ):
                return jsonify({"error": "Unauthorized access.", "code": "AUTH_REQUIRED"}), 401
            
            # Redirect unauthenticated browser GET page requests to signup
            return redirect(url_for("signup"))

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


