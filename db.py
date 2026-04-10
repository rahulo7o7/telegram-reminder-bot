import os
import logging
import psycopg2
from psycopg2 import pool

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

_pool: pool.SimpleConnectionPool | None = None


def _get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.SimpleConnectionPool(1, 5, DATABASE_URL, sslmode="require")
    return _pool


def _conn():
    return _get_pool().getconn()


def _put(conn):
    _get_pool().putconn(conn)


def init_db():
    conn = _conn()
    try:
        cur = conn.cursor()
        # Use TIMESTAMP WITH TIME ZONE so NOW() comparisons are always correct
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          SERIAL PRIMARY KEY,
                chat_id     BIGINT      NOT NULL,
                message     TEXT        NOT NULL,
                run_time    TIMESTAMPTZ NOT NULL,
                repeat_type TEXT        NOT NULL DEFAULT 'once',
                sent        BOOLEAN     NOT NULL DEFAULT FALSE
            )
        """)
        # Migrate existing table: if run_time column is TIMESTAMP (no tz), alter it
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'reminders'
                      AND column_name = 'run_time'
                      AND data_type = 'timestamp without time zone'
                ) THEN
                    -- Treat existing naive timestamps as UTC
                    ALTER TABLE reminders
                        ALTER COLUMN run_time TYPE TIMESTAMPTZ
                        USING run_time AT TIME ZONE 'UTC';
                END IF;
            END $$;
        """)
        conn.commit()
        cur.close()
        logger.info("DB initialised")
    finally:
        _put(conn)


def add_reminder(chat_id: int, message: str, run_time_str: str, repeat_type: str):
    """run_time_str is a UTC string 'YYYY-MM-DD HH:MM'."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reminders (chat_id, message, run_time, repeat_type) "
            "VALUES (%s, %s, %s::timestamptz, %s)",
            (chat_id, message, run_time_str + " UTC", repeat_type),
        )
        conn.commit()
        cur.close()
    finally:
        _put(conn)


def get_reminders(chat_id: int):
    """Return (id, message, run_time, repeat_type) for all non-sent reminders."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, message, run_time, repeat_type
            FROM reminders
            WHERE chat_id = %s AND sent = FALSE
            ORDER BY run_time
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        _put(conn)


def get_due_reminders():
    """Return all reminders due now (run_time <= NOW() UTC), not yet sent."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, chat_id, message, run_time, repeat_type "
            "FROM reminders WHERE run_time <= NOW() AND sent = FALSE"
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        _put(conn)


def mark_sent(reminder_id: int):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE reminders SET sent = TRUE WHERE id = %s", (reminder_id,))
        conn.commit()
        cur.close()
    finally:
        _put(conn)


def reschedule(reminder_id: int, repeat_type: str):
    """Advance run_time for repeating reminders and reset sent=FALSE."""
    interval_map = {
        "daily":   "1 day",
        "weekly":  "7 days",
        "monthly": "1 month",
    }
    interval = interval_map.get(repeat_type)
    if not interval:
        return  # one-time: leave as sent=TRUE

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE reminders SET run_time = run_time + INTERVAL %s, sent = FALSE WHERE id = %s",
            (interval, reminder_id),
        )
        conn.commit()
        cur.close()
    finally:
        _put(conn)


def delete_reminder(reminder_id: int, chat_id: int) -> bool:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM reminders WHERE id = %s AND chat_id = %s",
            (reminder_id, chat_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        return deleted
    finally:
        _put(conn)
