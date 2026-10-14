import os
import sqlite3
from pathlib import Path

from flask import g

DATABASE = os.environ.get("DATABASE_PATH", str(Path(__file__).with_name("database.db")))


def _connect():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(e=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def _columns(cursor, table):
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}


def _add_column(cursor, table, definition):
    name = definition.split()[0]
    if name not in _columns(cursor, table):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_db():
    """Initialize the canonical schema and apply narrow compatibility migrations.

    UTC is the storage policy for timestamp fields. Date-only deadlines remain
    date strings and are not converted into timestamps. due_date is nullable —
    a NULL value means the task is unscheduled.
    """
    connection = _connect()
    cursor = connection.cursor()
    try:
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                default_focus_mins INTEGER DEFAULT 25,
                break_mins INTEGER DEFAULT 5,
                ai_tone TEXT DEFAULT 'balanced',
                timezone TEXT DEFAULT 'UTC',
                focus_notes TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS planner_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                user_id INTEGER,
                parent_id INTEGER DEFAULT NULL,
                title TEXT NOT NULL,
                subject TEXT DEFAULT 'General',
                due_date TEXT DEFAULT NULL,
                due_time TEXT DEFAULT NULL,
                estimated_minutes INTEGER DEFAULT 30,
                priority INTEGER DEFAULT 2 CHECK(priority IN (1, 2, 3)),
                completed INTEGER DEFAULT 0,
                completed_at TIMESTAMP,
                is_deleted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(parent_id) REFERENCES planner_tasks(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                title TEXT NOT NULL,
                subject TEXT DEFAULT 'General',
                notes TEXT DEFAULT '',
                due_date TEXT DEFAULT NULL,
            );

            CREATE TABLE IF NOT EXISTS focus_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                task_id INTEGER,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                duration_minutes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'completed'
                    CHECK(status IN ('active', 'completed', 'abandoned')),
                mode TEXT DEFAULT 'pomodoro',
                paused_at TIMESTAMP,
                total_paused_seconds INTEGER NOT NULL DEFAULT 0,
                note TEXT DEFAULT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(task_id) REFERENCES planner_tasks(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                planner_task_id INTEGER DEFAULT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                location TEXT DEFAULT '',
                category TEXT DEFAULT 'General',
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                is_all_day INTEGER DEFAULT 0,
                source TEXT DEFAULT 'calendar',
                google_event_id TEXT DEFAULT NULL,
                is_deleted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(planner_task_id) REFERENCES planner_tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ai_request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS google_calendar_accounts (
                user_id INTEGER PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                google_calendar_id TEXT DEFAULT 'primary',
                last_synced_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS achievements (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                icon TEXT NOT NULL,
                requirement_type TEXT,
                requirement_value INTEGER
            );

            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id INTEGER NOT NULL,
                achievement_id TEXT NOT NULL,
                earned_at TIMESTAMP NOT NULL,
                PRIMARY KEY(user_id, achievement_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(achievement_id) REFERENCES achievements(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_planner_user_status
                ON planner_tasks(user_id, completed, is_deleted);
            CREATE INDEX IF NOT EXISTS idx_planner_due_date
                ON planner_tasks(user_id, due_date);
            CREATE INDEX IF NOT EXISTS idx_focus_user_completed
                ON focus_sessions(user_id, completed_at);
            CREATE INDEX IF NOT EXISTS idx_focus_active
                ON focus_sessions(user_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_focus
                ON focus_sessions(user_id) WHERE status = 'active';
            CREATE INDEX IF NOT EXISTS idx_events_user_time
                ON events(user_id, start_time, end_time);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_google_id
                ON events(google_event_id) WHERE google_event_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_ai_request_user_time
                ON ai_request_log(user_id, requested_at);
        """)

        # Narrow migrations for older installations.
        for definition in (
            "user_id INTEGER",
            "subject TEXT DEFAULT 'General'",
            "due_time TEXT DEFAULT NULL",
            "priority INTEGER DEFAULT 2",
            "completed INTEGER DEFAULT 0",
            "completed_at TIMESTAMP",
            "is_deleted INTEGER DEFAULT 0",
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ):
            _add_column(cursor, "planner_tasks", definition)

        for definition in (
            "user_id INTEGER",
            "started_at TIMESTAMP",
            "completed_at TIMESTAMP",
            "duration_minutes INTEGER DEFAULT 0",
            "status TEXT DEFAULT 'completed'",
            "mode TEXT DEFAULT 'pomodoro'",
            "paused_at TIMESTAMP",
            "total_paused_seconds INTEGER NOT NULL DEFAULT 0",
            "note TEXT DEFAULT NULL",
        ):
            _add_column(cursor, "focus_sessions", definition)

        for definition in (
            "user_id INTEGER",
            "planner_task_id INTEGER",
            "description TEXT DEFAULT ''",
            "location TEXT DEFAULT ''",
            "category TEXT DEFAULT 'General'",
            "is_all_day INTEGER DEFAULT 0",
            "source TEXT DEFAULT 'calendar'",
            "google_event_id TEXT",
            "is_deleted INTEGER DEFAULT 0",
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ):
            _add_column(cursor, "events", definition)

        for definition in (
            "email TEXT",
            "display_name TEXT",
            "default_focus_mins INTEGER DEFAULT 25",
            "break_mins INTEGER DEFAULT 5",
            "ai_tone TEXT DEFAULT 'balanced'",
            "timezone TEXT DEFAULT 'UTC'",
            "focus_notes TEXT DEFAULT NULL",
        ):
            _add_column(cursor, "users", definition)

        for definition in (
            "requirement_type TEXT",
            "requirement_value INTEGER",
        ):
            _add_column(cursor, "achievements", definition)

        # Backfill canonical immutable IDs from legacy username relationships.
        cursor.execute("""
            UPDATE planner_tasks
               SET user_id = (SELECT id FROM users WHERE users.username = planner_tasks.username)
             WHERE user_id IS NULL AND username IS NOT NULL
        """)
        cursor.execute("""
            UPDATE focus_sessions
               SET user_id = (SELECT id FROM users WHERE users.username = focus_sessions.username)
             WHERE user_id IS NULL AND username IS NOT NULL
        """)
        cursor.execute("""
            UPDATE events
               SET user_id = (SELECT id FROM users WHERE users.username = events.username)
             WHERE user_id IS NULL AND username IS NOT NULL
        """)

        # Normalize historical priority representations.
        cursor.execute("""
            UPDATE planner_tasks
               SET priority = CASE LOWER(CAST(priority AS TEXT))
                   WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2
                   WHEN 'low' THEN 3
                   ELSE CAST(priority AS INTEGER)
               END
             WHERE priority IS NOT NULL
        """)

        # A very old build used username as the user_achievements identity.
        legacy_cols = _columns(cursor, "user_achievements")
        if "username" in legacy_cols and "user_id" not in legacy_cols:
            cursor.execute("ALTER TABLE user_achievements RENAME TO user_achievements_legacy")
            cursor.execute("""
                CREATE TABLE user_achievements (
                    user_id INTEGER NOT NULL,
                    achievement_id TEXT NOT NULL,
                    earned_at TIMESTAMP NOT NULL,
                    PRIMARY KEY(user_id, achievement_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(achievement_id) REFERENCES achievements(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO user_achievements(user_id, achievement_id, earned_at)
                SELECT u.id, ua.achievement_id, ua.earned_at
                  FROM user_achievements_legacy ua
                  JOIN users u ON u.username = ua.username
            """)

        insert_achievements(connection)
        connection.commit()
    finally:
        connection.close()


def insert_achievements(conn):
    achievements = [
        # Session Count Milestones (10)
        ("first_session", "First Step", "Complete your first focus session", "🎉", "sessions", 1),
        ("sessions_5", "Getting Started", "Complete 5 focus sessions", "📘", "sessions", 5),
        ("sessions_10", "Building Momentum", "Complete 10 focus sessions", "📗", "sessions", 10),
        ("sessions_25", "Dedicated Student", "Complete 25 focus sessions", "📙", "sessions", 25),
        ("sessions_50", "Scholar", "Complete 50 focus sessions", "📚", "sessions", 50),
        ("sessions_75", "Knowledge Seeker", "Complete 75 focus sessions", "🎓", "sessions", 75),
        ("sessions_100", "Master Student", "Complete 100 focus sessions", "🏆", "sessions", 100),
        ("sessions_150", "Focus Veteran", "Complete 150 focus sessions", "🎖️", "sessions", 150),
        ("sessions_200", "Grandmaster", "Complete 200 focus sessions", "👑", "sessions", 200),
        ("sessions_500", "Legendary Scholar", "Complete 500 focus sessions", "🌟", "sessions", 500),

        # Total Minutes Logged (10)
        ("mins_30", "Half Hour Focus", "Log 30 total minutes of focus time", "⏱️", "minutes", 30),
        ("mins_100", "Century Club", "Log 100 total minutes of focus time", "⌚", "minutes", 100),
        ("mins_300", "Time Master", "Log 300 total minutes (5 hours) of focus time", "🕒", "minutes", 300),
        ("mins_600", "Ten Hour Club", "Log 600 total minutes (10 hours) of focus time", "⏳", "minutes", 600),
        ("mins_1200", "Day of Focus", "Log 1,200 total minutes (20 hours) of focus time", "💡", "minutes", 1200),
        ("mins_3000", "Fifty Hours", "Log 3,000 total minutes (50 hours) of focus time", "🎯", "minutes", 3000),
        ("mins_6000", "Centurion", "Log 6,000 total minutes (100 hours) of focus time", "🚀", "minutes", 6000),
        ("mins_12000", "Time Lord", "Log 12,000 total minutes (200 hours) of focus time", "🌌", "minutes", 12000),
        ("mins_30000", "Focus Sage", "Log 30,000 total minutes (500 hours) of focus time", "🧙‍♂️", "minutes", 30000),
        ("mins_60000", "1,000 Hour Master", "Log 60,000 total minutes (1,000 hours) of focus time", "💎", "minutes", 60000),

        # Streak Milestones (7)
        ("streak_2", "Duo", "Maintain a 2-day study streak", "🔥", "streak", 2),
        ("streak_3", "Building Habits", "Maintain a 3-day study streak", "🔥", "streak", 3),
        ("streak_7", "Unstoppable", "Maintain a 7-day study streak", "⚡", "streak", 7),
        ("streak_14", "Two Weeks Strong", "Maintain a 14-day study streak", "💪", "streak", 14),
        ("streak_30", "Monthly Consistency", "Maintain a 30-day study streak", "🗓️", "streak", 30),
        ("streak_60", "Iron Will", "Maintain a 60-day study streak", "🛡️", "streak", 60),
        ("streak_100", "Centennial Streak", "Maintain a 100-day study streak", "🌋", "streak", 100),

        # Mode Specific Challenges (8)
        ("pomo_1", "Pomodoro Novice", "Complete 1 Pomodoro session", "🍅", "pomodoro", 1),
        ("pomo_10", "Pomodoro Pro", "Complete 10 Pomodoro sessions", "🍅", "pomodoro", 10),
        ("pomo_50", "Pomodoro Master", "Complete 50 Pomodoro sessions", "🍅", "pomodoro", 50),
        ("deep_1", "Deep Thinker", "Complete 1 Deep Work session", "🧠", "deepwork", 1),
        ("deep_10", "Deep Explorer", "Complete 10 Deep Work sessions", "🧠", "deepwork", 10),
        ("quick_10", "Speed Learner", "Complete 10 Quick Burst sessions", "⚡", "quickburst", 10),
        ("exam_10", "Exam Warrior", "Complete 10 Exam Crunch sessions", "📖", "examcrunch", 10),
        ("exam_25", "Exam Conqueror", "Complete 25 Exam Crunch sessions", "🎓", "examcrunch", 25),

        # Daily Peak Milestones (5)
        ("daily_60", "Hour of Power", "Log 60+ focus minutes in a single day", "⚡", "dailyminutes", 60),
        ("daily_180", "Marathoner", "Log 180+ focus minutes (3 hrs) in a single day", "🏃", "dailyminutes", 180),
        ("daily_300", "Iron Focus", "Log 300+ focus minutes (5 hrs) in a single day", "🏋️", "dailyminutes", 300),
        ("daily_sessions_5", "High Output", "Complete 5+ focus sessions in a single day", "📊", "dailysessions", 5),
        ("daily_sessions_10", "Session Machine", "Complete 10+ focus sessions in a single day", "🤖", "dailysessions", 10),

        # Time-of-Day Challenges (4)
        ("early_1", "Early Bird", "Complete a focus session before 8:00 AM", "🌅", "early", 1),
        ("early_5", "Morning Routine", "Complete 5 focus sessions before 8:00 AM", "☕", "early", 5),
        ("night_1", "Night Owl", "Complete a focus session after 10:00 PM", "🌙", "night", 1),
        ("night_5", "Midnight Oil", "Complete 5 focus sessions after 10:00 PM", "🦉", "night", 5),

        # Tasks Created in Planner (4)
        ("first_task", "Getting Started", "Create your first task in the planner", "🎯", "tasks", 1),
        ("planner_5", "Task Organizer", "Create 5 active tasks in the planner", "📋", "tasks", 5),
        ("planner_pro", "Master Planner", "Create 10 active tasks in the planner", "📑", "tasks", 10),
        ("planner_50", "Planner Overlord", "Create 50 active tasks in the planner", "🗂️", "tasks", 50),

        # Account Longevity (2)
        ("account_1", "Welcome Aboard", "Have an active account for 1 day", "🌱", "account_age", 1),
        ("account_30", "One Month Veteran", "Have an active account for 30 days", "🌳", "account_age", 30),
    ]

    conn.executemany(
        """INSERT INTO achievements
           (id, title, description, icon, requirement_type, requirement_value)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               title = excluded.title,
               description = excluded.description,
               icon = excluded.icon,
               requirement_type = excluded.requirement_type,
               requirement_value = excluded.requirement_value""",
        achievements,
    )

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
