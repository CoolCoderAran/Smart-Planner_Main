import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, current_app
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from auth import require_auth
from validator import validate_schedule
import db


calendar_bp = Blueprint("calendar", __name__, url_prefix="/api/calendar")


# ---------------------------------------------------------------------------
# Google OAuth / Calendar API configuration
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI")

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"

GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.events"

GOOGLE_SYNC_WINDOW_DAYS_PAST = 30
GOOGLE_SYNC_WINDOW_DAYS_FUTURE = 180


def google_calendar_configured():
    return bool(
        GOOGLE_CLIENT_ID
        and GOOGLE_CLIENT_SECRET
        and GOOGLE_REDIRECT_URI
    )


def api_error(message, code, status):
    return jsonify({
        "error": message,
        "code": code,
    }), status


def _user_timezone(conn, user_id):
    row = conn.execute(
        "SELECT timezone FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    try:
        return ZoneInfo(
            row["timezone"]
            if row and row["timezone"]
            else "UTC"
        )
    except ZoneInfoNotFoundError:
        return timezone.utc


def _format_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _feed_time(value, is_all_day):
    if is_all_day or not value or len(str(value)) == 10:
        return value

    return str(value).replace(" ", "T") + "Z"


def _request_boundary(value):
    try:
        parsed = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            return str(value)[:19].replace("T", " ")

        return _format_utc(parsed)

    except (TypeError, ValueError):
        return str(value)[:19].replace("T", " ")


def validate_event_input(data, *, user_tz=timezone.utc):
    title = str(data.get("title", "")).strip()

    if not title:
        return None, "Event title cannot be empty."

    if len(title) > 255:
        return None, "Event title exceeds 255 characters."

    description = str(data.get("description", "")).strip()
    location = str(data.get("location", "")).strip()
    category = str(data.get("category", "General")).strip() or "General"

    is_all_day = (
        1
        if data.get("is_all_day") in [True, 1, "1", "true"]
        else 0
    )

    start_raw = str(data.get("start_time", "")).strip()
    end_raw = str(data.get("end_time", "")).strip()

    try:
        if is_all_day:
            start_dt = datetime.strptime(
                start_raw.split("T")[0],
                "%Y-%m-%d",
            )

            end_source = (
                end_raw.split("T")[0]
                if end_raw
                else start_raw.split("T")[0]
            )

            end_dt = datetime.strptime(
                end_source,
                "%Y-%m-%d",
            )

            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(days=1)

        else:
            start_dt = datetime.strptime(
                start_raw,
                "%Y-%m-%dT%H:%M",
            ).replace(tzinfo=user_tz)

            end_dt = datetime.strptime(
                end_raw,
                "%Y-%m-%dT%H:%M",
            ).replace(tzinfo=user_tz)

            if end_dt <= start_dt:
                return None, "End time must be after start time."

    except (TypeError, ValueError):
        return None, (
            "Invalid date/time format. "
            "Expected ISO YYYY-MM-DDTHH:MM."
        )

    if is_all_day:
        start_out = start_dt.strftime("%Y-%m-%d")
        end_out = end_dt.strftime("%Y-%m-%d")
    else:
        start_out = _format_utc(start_dt)
        end_out = _format_utc(end_dt)

    return {
        "title": title,
        "description": description,
        "location": location,
        "category": category,
        "is_all_day": is_all_day,
        "start_time": start_out,
        "end_time": end_out,
    }, None


# ---------------------------------------------------------------------------
# Calendar page + feed
# ---------------------------------------------------------------------------

@calendar_bp.route("/calendar", methods=["GET"])
@require_auth
def calendar_view(user_id):
    return render_template("calendar.html")


@calendar_bp.route("/feed", methods=["GET"])
@require_auth
def get_feed(user_id):
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if not start_str or not end_str:
        return api_error(
            "Query parameters 'start' and 'end' are required",
            "MISSING_RANGE",
            400,
        )

    start_bound = _request_boundary(start_str)
    end_bound = _request_boundary(end_str)

    conn = db.get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            title,
            description,
            location,
            category,
            start_time,
            end_time,
            is_all_day,
            planner_task_id,
            google_event_id,
            'event' AS entity_type
        FROM events
        WHERE user_id = ?
          AND is_deleted = 0
          AND datetime(start_time) < datetime(?)
          AND datetime(end_time) > datetime(?)
        """,
        (user_id, end_bound, start_bound),
    )

    events = [dict(row) for row in cursor.fetchall()]

    for event in events:
        event["start_time"] = _feed_time(
            event["start_time"],
            event["is_all_day"],
        )
        event["end_time"] = _feed_time(
            event["end_time"],
            event["is_all_day"],
        )

    linked_task_ids = {
        row["planner_task_id"]
        for row in events
        if row["planner_task_id"]
    }

    cursor.execute(
        """
        SELECT
            id,
            title,
            subject AS category,
            due_date,
            due_time,
            estimated_minutes,
            priority,
            completed,
            'task' AS entity_type
        FROM planner_tasks
        WHERE user_id = ?
          AND is_deleted = 0
          AND due_date IS NOT NULL
        """,
        (user_id,),
    )

    raw_tasks = cursor.fetchall()
    projected_tasks = []

    for task in raw_tasks:
        if task["id"] in linked_task_ids:
            continue

        due_date = task["due_date"]
        due_time = task["due_time"] or "23:59"

        start_iso = f"{due_date} {due_time}:00"

        try:
            start_dt = datetime.strptime(
                start_iso,
                "%Y-%m-%d %H:%M:%S",
            )

            end_dt = start_dt + timedelta(
                minutes=task["estimated_minutes"] or 30
            )

            end_iso = end_dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        except ValueError:
            end_iso = start_iso

        if not (
            start_iso < end_bound
            and end_iso > start_bound
        ):
            continue

        projected_tasks.append({
            "id": task["id"],
            "title": task["title"],
            "description": (
                f"Priority: {task['priority']} | "
                f"Status: "
                f"{'Completed' if task['completed'] else 'Pending'}"
            ),
            "location": "",
            "category": task["category"] or "Task",
            "start_time": start_iso.replace(" ", "T"),
            "end_time": end_iso.replace(" ", "T"),
            "is_all_day": 0,
            "completed": task["completed"],
            "entity_type": "task",
        })

    combined_feed = events + projected_tasks

    return jsonify({
        "feed": combined_feed,
    }), 200


# ---------------------------------------------------------------------------
# CRUD for Calendar Events
# ---------------------------------------------------------------------------

@calendar_bp.route("/events/add", methods=["POST"])
@require_auth
def add_calendar_event(user_id):
    payload = (
        request.get_json(silent=True)
        or request.form.to_dict()
    )

    conn = db.get_db()

    data, error = validate_event_input(
        payload,
        user_tz=_user_timezone(conn, user_id),
    )

    if error:
        return api_error(
            error,
            "VALIDATION_ERROR",
            400,
        )

    planner_task_id = (
        payload.get("task_id")
        or payload.get("planner_task_id")
    )

    if planner_task_id in ("", None):
        planner_task_id = None

    else:
        try:
            planner_task_id = int(planner_task_id)
        except (TypeError, ValueError):
            return api_error(
                "Invalid task_id.",
                "VALIDATION_ERROR",
                400,
            )

        owned = conn.execute(
            """
            SELECT id
            FROM planner_tasks
            WHERE id = ?
              AND user_id = ?
              AND is_deleted = 0
            """,
            (planner_task_id, user_id),
        ).fetchone()

        if not owned:
            return api_error(
                "Task not found or permission denied.",
                "TASK_NOT_FOUND",
                404,
            )

    username_row = conn.execute(
        "SELECT username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    username = (
        username_row["username"]
        if username_row
        else None
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO events (
            user_id,
            username,
            planner_task_id,
            title,
            description,
            location,
            category,
            start_time,
            end_time,
            is_all_day,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'calendar')
        """,
        (
            user_id,
            username,
            planner_task_id,
            data["title"],
            data["description"],
            data["location"],
            data["category"],
            data["start_time"],
            data["end_time"],
            data["is_all_day"],
        ),
    )

    event_id = cursor.lastrowid

    conn.commit()

    return jsonify({
        "message": "Event created",
        "event_id": event_id,
    }), 201


@calendar_bp.route("/events/update/<int:event_id>", methods=["POST"])
@require_auth
def update_calendar_event(user_id, event_id):
    payload = (
        request.get_json(silent=True)
        or request.form.to_dict()
    )

    conn = db.get_db()

    data, error = validate_event_input(
        payload,
        user_tz=_user_timezone(conn, user_id),
    )

    if error:
        return api_error(
            error,
            "VALIDATION_ERROR",
            400,
        )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM events
        WHERE id = ?
          AND user_id = ?
          AND is_deleted = 0
        """,
        (event_id, user_id),
    )

    if not cursor.fetchone():
        return api_error(
            "Event not found.",
            "EVENT_NOT_FOUND",
            404,
        )

    cursor.execute(
        """
        UPDATE events
        SET
            title = ?,
            description = ?,
            location = ?,
            category = ?,
            start_time = ?,
            end_time = ?,
            is_all_day = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND user_id = ?
        """,
        (
            data["title"],
            data["description"],
            data["location"],
            data["category"],
            data["start_time"],
            data["end_time"],
            data["is_all_day"],
            event_id,
            user_id,
        ),
    )

    conn.commit()

    return jsonify({
        "message": "Event updated successfully",
    }), 200


@calendar_bp.route(
    "/events/delete/<int:event_id>",
    methods=["DELETE", "POST"],
)
@require_auth
def delete_calendar_event(user_id, event_id):
    conn = db.get_db()

    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE events
        SET
            is_deleted = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND user_id = ?
          AND is_deleted = 0
        """,
        (event_id, user_id),
    )

    if cursor.rowcount == 0:
        return api_error(
            "Event not found or permission denied.",
            "EVENT_NOT_FOUND",
            404,
        )

    conn.commit()

    return jsonify({
        "message": "Event deleted successfully",
    }), 200


@calendar_bp.route("/apply-schedule", methods=["POST"])
@require_auth
def apply_schedule(user_id):
    payload = request.get_json(silent=True) or {}
    blocks = payload.get("blocks")

    valid, sanitized, error = validate_schedule(blocks)

    if not valid:
        return api_error(
            error,
            "VALIDATION_ERROR",
            400,
        )

    if not sanitized:
        return api_error(
            "No schedule blocks to apply.",
            "EMPTY_SCHEDULE",
            400,
        )

    conn = db.get_db()

    user_tz = _user_timezone(conn, user_id)

    username_row = conn.execute(
        "SELECT username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    username = (
        username_row["username"]
        if username_row
        else None
    )

    created_ids = []

    for block in sanitized:
        task_id = block.get("task_id")

        if task_id is not None:
            try:
                task_id = int(task_id)
            except (TypeError, ValueError):
                task_id = None
            else:
                owned = conn.execute(
                    """
                    SELECT id
                    FROM planner_tasks
                    WHERE id = ?
                      AND user_id = ?
                      AND is_deleted = 0
                    """,
                    (task_id, user_id),
                ).fetchone()

                if not owned:
                    task_id = None

        start_dt = datetime.strptime(
            f"{block['date']} {block['start_time']}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=user_tz)

        end_dt = datetime.strptime(
            f"{block['date']} {block['end_time']}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=user_tz)

        if end_dt <= start_dt:
            return api_error(
                "Schedule block end time must be after start time.",
                "INVALID_BLOCK_TIME",
                400,
            )

        start_time = _format_utc(start_dt)
        end_time = _format_utc(end_dt)

        cursor = conn.execute(
            """
            INSERT INTO events (
                user_id,
                username,
                planner_task_id,
                title,
                description,
                location,
                category,
                start_time,
                end_time,
                is_all_day,
                source
            )
            VALUES (?, ?, ?, ?, '', '', 'AI Schedule', ?, ?, 0, 'ai')
            """,
            (
                user_id,
                username,
                task_id,
                block["title"],
                start_time,
                end_time,
            ),
        )

        created_ids.append(cursor.lastrowid)

    conn.commit()

    return jsonify({
        "message": (
            f"Applied {len(created_ids)} "
            "schedule block(s) to your Calendar."
        ),
        "event_ids": created_ids,
    }), 201


# ---------------------------------------------------------------------------
# Google Calendar OAuth + Sync
# ---------------------------------------------------------------------------

def _get_google_account(conn, user_id):
    return conn.execute(
        """
        SELECT *
        FROM google_calendar_accounts
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()


def _save_google_tokens(
    conn,
    user_id,
    access_token,
    refresh_token,
    expires_in,
    calendar_id="primary",
):
    expiry = (
        datetime.now(timezone.utc)
        + timedelta(seconds=int(expires_in or 3600))
    ).isoformat(timespec="seconds")

    existing = _get_google_account(conn, user_id)

    if existing:
        conn.execute(
            """
            UPDATE google_calendar_accounts
            SET
                access_token = ?,
                refresh_token = COALESCE(?, refresh_token),
                token_expiry = ?,
                google_calendar_id = ?
            WHERE user_id = ?
            """,
            (
                access_token,
                refresh_token,
                expiry,
                calendar_id,
                user_id,
            ),
        )

    else:
        conn.execute(
            """
            INSERT INTO google_calendar_accounts (
                user_id,
                access_token,
                refresh_token,
                token_expiry,
                google_calendar_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                access_token,
                refresh_token,
                expiry,
                calendar_id,
            ),
        )

    conn.commit()


def _refresh_access_token(conn, user_id, account):
    if not account["refresh_token"]:
        return None

    try:
        response = requests.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": account["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        current_app.logger.error(
            "Google Calendar token refresh failed: "
            "status=%s, response=%s",
            response.status_code,
            (response.text or "")[:1000],
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    access_token = payload.get("access_token")

    if not access_token:
        return None

    _save_google_tokens(
        conn,
        user_id,
        access_token,
        None,
        payload.get("expires_in"),
        account["google_calendar_id"],
    )

    return access_token


def _valid_access_token(conn, user_id):
    account = _get_google_account(conn, user_id)

    if not account:
        return None, None

    try:
        expiry = datetime.fromisoformat(
            str(account["token_expiry"])
        )

        if expiry.tzinfo is None:
            expiry = expiry.replace(
                tzinfo=timezone.utc
            )

    except (TypeError, ValueError):
        expiry = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )

    if expiry > datetime.now(timezone.utc) + timedelta(
        seconds=30
    ):
        return account["access_token"], account

    refreshed = _refresh_access_token(
        conn,
        user_id,
        account,
    )

    if not refreshed:
        return None, account

    account = _get_google_account(conn, user_id)

    return refreshed, account


@calendar_bp.route("/google/status", methods=["GET"])
@require_auth
def google_status(user_id):
    if not google_calendar_configured():
        return jsonify({
            "configured": False,
            "connected": False,
        })

    conn = db.get_db()

user = conn.execute(
    "SELECT id FROM users WHERE id = ?",
    (user_id,),
).fetchone()

if user is None:
    current_app.logger.error(
        "Google OAuth failed: user_id=%r is missing from users table",
        user_id,
    )
    return redirect(url_for("login"))  # Use your actual login endpoint name.

user_id = user["id"]

_save_google_tokens(
    conn,
    user_id,
    access_token,
    payload.get("refresh_token"),
    payload.get("expires_in"),
)

    return jsonify({
        "configured": True,
        "connected": account is not None,
        "last_synced_at": (
            account["last_synced_at"]
            if account
            else None
        ),
    })


@calendar_bp.route("/google/connect", methods=["GET"])
@require_auth
def google_connect(user_id):
    if not google_calendar_configured():
        return api_error(
            "Google Calendar sync is not configured on this server. "
            "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
            "GOOGLE_REDIRECT_URI.",
            "GOOGLE_NOT_CONFIGURED",
            503,
        )

    state = secrets.token_urlsafe(24)

    session["google_oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    return redirect(
        f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"
    )


@calendar_bp.route("/google/callback", methods=["GET"])
@require_auth
def google_callback(user_id):
    if not google_calendar_configured():
        return api_error(
            "Google Calendar sync is not configured on this server.",
            "GOOGLE_NOT_CONFIGURED",
            503,
        )

    error = request.args.get("error")

    if error:
        return redirect(
            url_for(
                "calendar.calendar_view",
                gcal_error=error,
            )
        )

    state = request.args.get("state")

    expected_state = session.pop(
        "google_oauth_state",
        None,
    )

    if not state or state != expected_state:
        return api_error(
            "Invalid OAuth state.",
            "OAUTH_STATE_MISMATCH",
            400,
        )

    code = request.args.get("code")

    if not code:
        return api_error(
            "Missing authorization code.",
            "OAUTH_CODE_MISSING",
            400,
        )

    try:
        response = requests.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_REDIRECT_URI,
            },
            timeout=10,
        )
    except requests.RequestException:
        return redirect(
            url_for(
                "calendar.calendar_view",
                gcal_error="token_exchange_failed",
            )
        )

    if response.status_code != 200:
        return redirect(
            url_for(
                "calendar.calendar_view",
                gcal_error="token_exchange_failed",
            )
        )

    try:
        payload = response.json()
    except ValueError:
        return redirect(
            url_for(
                "calendar.calendar_view",
                gcal_error="invalid_token_response",
            )
        )

    access_token = payload.get("access_token")

    if not access_token:
        return redirect(
            url_for(
                "calendar.calendar_view",
                gcal_error="missing_access_token",
            )
        )

    conn = db.get_db()

    _save_google_tokens(
        conn,
        user_id,
        access_token,
        payload.get("refresh_token"),
        payload.get("expires_in"),
    )

    return redirect(
        url_for(
            "calendar.calendar_view",
            gcal_connected="1",
        )
    )


@calendar_bp.route("/google/disconnect", methods=["POST"])
@require_auth
def google_disconnect(user_id):
    conn = db.get_db()

    conn.execute(
        """
        DELETE FROM google_calendar_accounts
        WHERE user_id = ?
        """,
        (user_id,),
    )

    conn.commit()

    return jsonify({
        "message": "Google Calendar disconnected.",
    }), 200


def _google_event_to_local(item, calendar_id):
    """Map a Google Calendar API event to local event fields."""

    start = item.get("start", {})
    end = item.get("end", {})

    is_all_day = "date" in start

    if is_all_day:
        start_time = start.get("date")
        end_time = end.get("date")

    else:
        try:
            start_dt = datetime.fromisoformat(
                (start.get("dateTime") or "").replace(
                    "Z",
                    "+00:00",
                )
            )

            end_dt = datetime.fromisoformat(
                (end.get("dateTime") or "").replace(
                    "Z",
                    "+00:00",
                )
            )

            start_time = _format_utc(start_dt)
            end_time = _format_utc(end_dt)

        except (TypeError, ValueError):
            start_time = ""
            end_time = ""

    return {
        "title": (
            item.get("summary")
            or "(No title)"
        )[:255],

        "description": (
            item.get("description")
            or ""
        )[:2000],

        "location": (
            item.get("location")
            or ""
        )[:255],

        "category": "Google Calendar",

        "start_time": start_time,
        "end_time": end_time,

        "is_all_day": (
            1
            if is_all_day
            else 0
        ),
    }


@calendar_bp.route("/google/sync", methods=["POST"])
@require_auth
def google_sync(user_id):
    if not google_calendar_configured():
        return api_error(
            "Google Calendar sync is not configured on this server.",
            "GOOGLE_NOT_CONFIGURED",
            503,
        )

    conn = db.get_db()

    access_token, account = _valid_access_token(
        conn,
        user_id,
    )

    if not access_token or not account:
        return api_error(
            "Google Calendar is not connected. "
            "Please connect your account first.",
            "GOOGLE_NOT_CONNECTED",
            409,
        )

    calendar_id = (
        account["google_calendar_id"]
        or "primary"
    )

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    now = datetime.now(timezone.utc)

    time_min = (
        now - timedelta(
            days=GOOGLE_SYNC_WINDOW_DAYS_PAST
        )
    ).isoformat()

    time_max = (
        now + timedelta(
            days=GOOGLE_SYNC_WINDOW_DAYS_FUTURE
        )
    ).isoformat()

    pulled = 0
    pushed = 0

    try:
        page_token = None

        while True:
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "maxResults": 250,
            }

            if page_token:
                params["pageToken"] = page_token

            response = requests.get(
                f"{GOOGLE_CALENDAR_API}/calendars/"
                f"{calendar_id}/events",
                headers=headers,
                params=params,
                timeout=15,
            )

            if response.status_code == 401:
                return api_error(
                    "Google Calendar authorization expired. "
                    "Please reconnect.",
                    "GOOGLE_AUTH_EXPIRED",
                    401,
                )

            if response.status_code != 200:
                current_app.logger.error(
                    "Google Calendar events request failed: "
                    "status=%s body=%s",
                    response.status_code,
                    (response.text or "")[:1000],
                )

                return api_error(
                    "Could not fetch events from Google Calendar.",
                    "GOOGLE_FETCH_FAILED",
                    502,
                )

            try:
                body = response.json()
            except ValueError:
                return api_error(
                    "Google Calendar returned invalid data.",
                    "GOOGLE_INVALID_RESPONSE",
                    502,
                )

            for item in body.get("items", []):
                google_event_id = item.get("id")

                if not google_event_id:
                    continue

                if item.get("status") == "cancelled":
                    conn.execute(
                        """
                        UPDATE events
                        SET is_deleted = 1
                        WHERE user_id = ?
                          AND google_event_id = ?
                        """,
                        (
                            user_id,
                            google_event_id,
                        ),
                    )
                    continue

                mapped = _google_event_to_local(
                    item,
                    calendar_id,
                )

                if (
                    not mapped["start_time"]
                    or not mapped["end_time"]
                ):
                    continue

                existing = conn.execute(
                    """
                    SELECT id
                    FROM events
                    WHERE user_id = ?
                      AND google_event_id = ?
                    """,
                    (
                        user_id,
                        google_event_id,
                    ),
                ).fetchone()

                if existing:
                    conn.execute(
                        """
                        UPDATE events
                        SET
                            title = ?,
                            description = ?,
                            location = ?,
                            start_time = ?,
                            end_time = ?,
                            is_all_day = ?,
                            is_deleted = 0,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            mapped["title"],
                            mapped["description"],
                            mapped["location"],
                            mapped["start_time"],
                            mapped["end_time"],
                            mapped["is_all_day"],
                            existing["id"],
                        ),
                    )

                else:
                    conn.execute(
                        """
                        INSERT INTO events (
                            user_id,
                            title,
                            description,
                            location,
                            category,
                            start_time,
                            end_time,
                            is_all_day,
                            source,
                            google_event_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'google', ?)
                        """,
                        (
                            user_id,
                            mapped["title"],
                            mapped["description"],
                            mapped["location"],
                            mapped["category"],
                            mapped["start_time"],
                            mapped["end_time"],
                            mapped["is_all_day"],
                            google_event_id,
                        ),
                    )

                pulled += 1

            page_token = body.get("nextPageToken")

            if not page_token:
                break

        conn.commit()

    except requests.RequestException:
        return api_error(
            "Network error while syncing with Google Calendar.",
            "GOOGLE_NETWORK_ERROR",
            502,
        )

    try:
        local_only = conn.execute(
            """
            SELECT *
            FROM events
            WHERE user_id = ?
              AND is_deleted = 0
              AND source = 'calendar'
              AND google_event_id IS NULL
            """,
            (user_id,),
        ).fetchall()

        for row in local_only:
            if row["is_all_day"]:
                google_body = {
                    "summary": row["title"],
                    "description": row["description"] or "",
                    "location": row["location"] or "",
                    "start": {
                        "date": row["start_time"][:10]
                    },
                    "end": {
                        "date": row["end_time"][:10]
                    },
                }

            else:
                google_body = {
                    "summary": row["title"],
                    "description": row["description"] or "",
                    "location": row["location"] or "",
                    "start": {
                        "dateTime": (
                            row["start_time"]
                            .replace(" ", "T")
                            + "Z"
                        ),
                        "timeZone": "UTC",
                    },
                    "end": {
                        "dateTime": (
                            row["end_time"]
                            .replace(" ", "T")
                            + "Z"
                        ),
                        "timeZone": "UTC",
                    },
                }

            response = requests.post(
                f"{GOOGLE_CALENDAR_API}/calendars/"
                f"{calendar_id}/events",
                headers=headers,
                json=google_body,
                timeout=15,
            )

            if response.status_code not in (200, 201):
                continue

            try:
                google_id = response.json().get("id")
            except ValueError:
                google_id = None

            if not google_id:
                continue

            conn.execute(
                """
                UPDATE events
                SET google_event_id = ?
                WHERE id = ?
                """,
                (
                    google_id,
                    row["id"],
                ),
            )

            pushed += 1

        conn.commit()

    except requests.RequestException:
        return api_error(
            "Network error while syncing with Google Calendar.",
            "GOOGLE_NETWORK_ERROR",
            502,
        )

    conn.execute(
        """
        UPDATE google_calendar_accounts
        SET last_synced_at = ?
        WHERE user_id = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            user_id,
        ),
    )

    conn.commit()

    return jsonify({
        "message": (
            f"Synced with Google Calendar: "
            f"{pulled} pulled, {pushed} pushed."
        ),
        "pulled": pulled,
        "pushed": pushed,
    }), 200
