import os
import logging
import psycopg2
from psycopg2 import pool

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

# Connection pool (min 1, max 5) — avoids opening a new connection on every call
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          SERIAL PRIMARY KEY,
                chat_id     BIGINT      NOT NULL,
                message     TEXT        NOT NULL,
                run_time    TIMESTAMP   NOT NULL,   -- stored as proper timestamp
                repeat_type TEXT        NOT NULL,   -- 'once' | 'weekly' | 'monthly'
                sent        BOOLEAN     NOT NULL DEFAULT FALSE
            )
        """)
        conn.commit()
        cur.close()
        logger.info("DB initialised")
    finally:
        _put(conn)


def add_reminder(chat_id: int, message: str, run_time_str: str, repeat_type: str):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO reminders (chat_id, message, run_time, repeat_type)
            VALUES (%s, %s, %s::timestamp, %s)
            """,
            (chat_id, message, run_time_str, repeat_type),
        )
        conn.commit()
        cur.close()
    finally:
        _put(conn)


def get_reminders(chat_id: int):
    """Return (id, message, run_time, repeat_type) for a user's reminders."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, message, run_time, repeat_type FROM reminders WHERE chat_id = %s ORDER BY run_time",
            (chat_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        _put(conn)


def get_due_reminders():
    """Return all reminders that are due now and not yet marked sent."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, chat_id, message, run_time, repeat_type
            FROM reminders
            WHERE run_time <= NOW() AND sent = FALSE
            """
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
    """Advance run_time for weekly/monthly reminders and reset sent=FALSE."""
    if repeat_type == "weekly":
        interval = "7 days"
    elif repeat_type == "monthly":
        interval = "1 month"
    else:
        return  # one-time: leave as sent

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE reminders SET run_time = run_time + INTERVAL '{interval}', sent = FALSE WHERE id = %s",
            (reminder_id,),
        )
        conn.commit()
        cur.close()
    finally:
        _put(conn)
