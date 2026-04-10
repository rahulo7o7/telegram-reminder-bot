# Telegram Reminder Bot

A fully working Telegram reminder bot deployable on Railway.

## Features
- ➕ Create one-time, daily, weekly, or monthly reminders
- 📋 List all upcoming reminders
- 🗑 Delete reminders via inline buttons
- ❌ Cancel mid-flow with /cancel
- ⏰ Reminders fire within 30 seconds of scheduled time
- 🔁 Repeating reminders auto-reschedule after firing

## Setup

### 1. Environment Variables (set in Railway)

| Variable | Value |
|---|---|
| `TOKEN` | Your BotFather token |
| `WEBHOOK_URL` | `https://your-app.up.railway.app` (no trailing slash) |
| `DATABASE_URL` | Auto-set by Railway Postgres plugin |

### 2. Railway Deploy Steps

```bash
# Push to GitHub
git init && git add . && git commit -m "init"
gh repo create reminder-bot --public --source=. --push

# Railway: New Project → Deploy from GitHub repo
# Railway: + New → Database → PostgreSQL  (DATABASE_URL auto-injected)
# Railway: Variables → add TOKEN + WEBHOOK_URL
```

### 3. Local Testing with ngrok

```bash
pip install -r requirements.txt
export TOKEN="..." DATABASE_URL="postgresql://..." WEBHOOK_URL="https://xxxx.ngrok-free.app"
ngrok http 8000
uvicorn main:app --reload --port 8000
```

## Time Zone Note

The bot stores and compares times in UTC.  
IST = UTC+5:30, so 9:00 AM IST → enter `03:30` UTC.

## Architecture

```
Telegram → HTTPS webhook → FastAPI /webhook → PTB process_update → handlers
                                                                         ↓
APScheduler (every 30s) → get_due_reminders() → bot.send_message()
```

## Bot Commands

| Command | Description |
|---|---|
| /start | Welcome message |
| /new | Create a new reminder |
| /list | View upcoming reminders |
| /delete | Delete a reminder |
| /cancel | Cancel current action |
| /help | Help & time zone tips |
