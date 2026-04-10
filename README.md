# Telegram Reminder Bot — Railway Deployment

## What's fixed vs your original

| Problem | Fix |
|---|---|
| `AttributeError: 'Updater'...` | Use `.updater(None)` when building Application (webhook mode doesn't use Updater) |
| `@app.on_event` deprecation warning | Replaced with FastAPI `lifespan` context manager |
| No actual reminder sending | Added APScheduler checking every 60s for due reminders |
| DB opened/closed per call | Connection pool via `psycopg2.pool.SimpleConnectionPool` |
| `run_time` stored as TEXT | Now stored as proper `TIMESTAMP` column — enables `<= NOW()` comparison |
| `sent` flag missing | Added `sent` column + `reschedule()` for weekly/monthly auto-advance |
| Syntax error in db.py | Fixed duplicate `conn.commit()` lines |

---

## Environment Variables (set in Railway)

| Variable | Example |
|---|---|
| `TOKEN` | `7123456789:AAF...` |
| `WEBHOOK_URL` | `https://your-app.up.railway.app` |
| `DATABASE_URL` | `postgresql://postgres:pass@host:5432/railway` |

> **WEBHOOK_URL** = your Railway public domain (no trailing slash, no `/webhook`).  
> Railway sets `DATABASE_URL` automatically when you add a Postgres plugin.

---

## Deploy steps

```bash
# 1. Push to GitHub
git init && git add . && git commit -m "init"
gh repo create reminder-bot --public --source=. --push

# 2. Create Railway project from the repo
#    (New Project → Deploy from GitHub repo)

# 3. Add Railway Postgres plugin
#    (+ New → Database → PostgreSQL)
#    DATABASE_URL is injected automatically.

# 4. Set TOKEN and WEBHOOK_URL in Railway → Variables tab

# 5. Deploy — Railway runs:
#    uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## Local testing

```bash
pip install -r requirements.txt

export TOKEN="your_bot_token"
export DATABASE_URL="postgresql://user:pass@localhost:5432/reminders"
export WEBHOOK_URL="https://your-ngrok-or-tunnel-url"

uvicorn main:app --reload --port 8000
```

Use **ngrok** (`ngrok http 8000`) to get a public HTTPS URL for local webhook testing.

---

## How reminders work

1. User sends `/new`, enters message, time (`YYYY-MM-DD HH:MM`), and repeat type.
2. Reminder is saved to PostgreSQL with `sent=FALSE`.
3. APScheduler checks every 60 seconds: any reminder where `run_time <= NOW()` and `sent=FALSE`?
4. If yes → sends Telegram message → marks `sent=TRUE`.
5. For weekly/monthly reminders → `run_time` is advanced by 7 days / 1 month and `sent` reset to `FALSE`.
