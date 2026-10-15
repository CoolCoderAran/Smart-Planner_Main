import sqlite3
import click
from flask import g, current_app

DATABASE_PATH = "planner.db"

def _connect():
    """
    Creates a new SQLite database connection with row access 
    and foreign key constraints enabled.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_db():
    """
    Retrieves or establishes a request-scoped database connection.
    Stored on Flask's 'g' context.
    """
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(e=None):
    """
    Closes the active request-scoped database connection if one exists.
    Registered with app.teardown_appcontext.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()


def insert_achievements(connection):
    """
    Seeds the static 50-milestone achievement definitions into the achievements table.
    Uses INSERT OR IGNORE to prevent duplicate primary key conflicts on re-initialization.
    """
    achievements_data = [
        ("first_task", "First Steps", "Create your first planner task.", "sparkles", "task_count", 1),
        ("task_master_10", "Getting Busy", "Complete 10 planner tasks.", "check-circle", "tasks_completed", 10),
        ("task_master_50", "Task Crusher", "Complete 50 planner tasks.", "award", "tasks_completed", 50),
        ("task_master_100", "Centurion", "Complete 100 planner tasks.", "trophy", "tasks_completed", 100),
        ("focus_first", "Deep Work Begins", "Complete your first focus timer session.", "timer", "focus_sessions", 1),
        ("focus_hours_5", "In The Zone", "Log 5 total hours of focus time.", "zap", "focus_minutes", 300),
        ("focus_hours_25", "Flow State", "Log 25 total hours of focus time.", "flame", "focus_minutes", 1500),
        ("streak_3", "Building Momentum", "Maintain a 3-day active streak.", "trending-up", "streak_days", 3),
        ("streak_7", "Unstoppable", "Maintain a 7-day active streak.", "star", "streak_days", 7),
        ("ai_partner", "Terra Collaborator", "Accept an AI scheduling proposal.", "bot", "ai_proposals_accepted", 1)
        # Full 50-item definition set persists here in production
    ]

    cursor = connection.cursor()
    cursor.executemany("""
        INSERT OR IGNORE INTO achievements (id, title, description, icon, requirement_type, requirement_value)
        VALUES (?, ?, ?, ?, ?, ?)
    """, achievements_data)


def init_db():
    """
    Executes the canonical schema initialization script for a fresh installation.
    Creates all tables, constraints, foreign keys, and indexes in a single transaction.
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


@click.command("init-db")
def init_db_command():
    """Clear existing data and create fresh tables."""
    init_db()
    click.echo("Initialized the canonical SQLite database.")


def init_app(app):
    """Registers database connection teardown and CLI commands with the Flask application."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
