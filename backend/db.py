import sqlite3
from flask import g

DATABASE_PATH = "planner.db"

def _connect():
    """Helper to create a new database connection with foreign keys enabled."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def get_db():
    """Retrieves or creates a request-scoped database connection."""
    if "db" not in g:
        g.db = _connect()
    return g.db

def close_db(e=None):
    """Closes the request-scoped database connection if open."""
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """Initialize canonical database schema for a fresh start."""
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
                notes TEXT DEFAULT '',
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

        insert_achievements(connection)
        connection.commit()
    finally:
        connection.close()
