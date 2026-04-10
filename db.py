import os
import logging
import psycopg2
from psycopg2 import pool

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
_pool: pool.SimpleConnectionPool | None = None


def _get_pool():
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

        # Create table if it doesn't exist at all
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
        conn.commit()

        # Check actual column type and fix if it's TEXT or plain TIMESTAMP
        cur.execute("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'reminders' AND column_name = 'run_time'
        """)
        row = cur.fetchone()
        col_type = row[0] if row else None
        logger.info(f"run_time column type: {col_type}")

        if col_type == 'text':
            logger.warning("run_time is TEXT — migrating to TIMESTAMPTZ (treating as UTC)")
            cur.execute("""
                ALTER TABLE reminders
                    ALTER COLUMN run_time TYPE TIMESTAMPTZ
                    USING run_time::timestamp AT TIME ZONE 'UTC'
            """)
            conn.commit()
            logger.info("✅ Migrated run_time: TEXT → TIMESTAMPTZ")

        elif col_type == 'timestamp without time zone':
            logger.warning("run_time is TIMESTAMP (no tz) — migrating to TIMESTAMPTZ")
            cur.execute("""
                ALTER TABLE reminders
                    ALTER COLUMN run_time TYPE TIMESTAMPTZ
                    USING run_time AT TIME ZONE 'UTC'
            """)
            conn.commit()
            logger.info("✅ Migrated run_time: TIMESTAMP → TIMESTAMPTZ")

        else:
            logger.info("✅ run_time column type is correct (TIMESTAMPTZ)")

        cur.close()
    finally:
        _put(conn)


def add_reminder(chat_id: int, message: str, run_time_utc_str: str, repeat_type: str):
    """run_time_utc_str: 'YYYY-MM-DD HH:MM' in UTC."""
    conn = _conn()
    try:
        cur = conn.cursor()
        # Explicit UTC tag prevents any ambiguity
        cur.execute(
            "INSERT INTO reminders (chat_id, message, run_time, repeat_type) "
            "VALUES (%s, %s, (%s || ' UTC')::timestamptz, %s)",
            (chat_id, message, run_time_utc_str, repeat_type),
        )
        conn.commit()
        cur.close()
        logger.info(f"Saved reminder chat={chat_id} time={run_time_utc_str} UTC type={repeat_type}")
    finally:
        _put(conn)


def get_reminders(chat_id: int):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, message, run_time, repeat_type FROM reminders "
            "WHERE chat_id = %s AND sent = FALSE ORDER BY run_time",
            (chat_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        _put(conn)


def get_due_reminders():
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
    interval_map = {"daily": "1 day", "weekly": "7 days", "monthly": "1 month"}
    interval = interval_map.get(repeat_type)
    if not interval:
        return
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
        cur.execute("DELETE FROM reminders WHERE id = %s AND chat_id = %s", (reminder_id, chat_id))
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        return deleted
    finally:
        _put(conn)
