"""
Run this ONCE on Railway to fix the database schema.
Usage: python migrate.py

This fixes: ERROR: operator does not exist: text <= timestamp with time zone
Cause: run_time column was stored as TEXT, not TIMESTAMP.
"""
import os
import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]

conn = psycopg2.connect(DATABASE_URL, sslmode="require")
cur = conn.cursor()

# 1. Check current column type
cur.execute("""
    SELECT data_type FROM information_schema.columns
    WHERE table_name = 'reminders' AND column_name = 'run_time'
""")
row = cur.fetchone()
print(f"Current run_time type: {row[0] if row else 'COLUMN NOT FOUND'}")

# 2. Fix it — cast TEXT → TIMESTAMPTZ, treating stored values as UTC
cur.execute("""
    ALTER TABLE reminders
        ALTER COLUMN run_time TYPE TIMESTAMPTZ
        USING run_time::timestamp AT TIME ZONE 'UTC'
""")
conn.commit()
print("✅ Migration done — run_time is now TIMESTAMPTZ")

# 3. Verify
cur.execute("""
    SELECT data_type FROM information_schema.columns
    WHERE table_name = 'reminders' AND column_name = 'run_time'
""")
row = cur.fetchone()
print(f"New run_time type: {row[0]}")

# 4. Show current reminders
cur.execute("SELECT id, chat_id, message, run_time, repeat_type, sent FROM reminders ORDER BY run_time")
rows = cur.fetchall()
print(f"\nReminders in DB ({len(rows)} total):")
for r in rows:
    print(f"  id={r[0]} chat={r[1]} msg='{r[2]}' time={r[3]} type={r[4]} sent={r[5]}")

cur.close()
conn.close()
