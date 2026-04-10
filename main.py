import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from db import init_db, add_reminder, get_reminders
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")  # e.g. https://your-app.railway.app

# ── Build the PTB Application (no job queue; we use APScheduler instead) ──────
telegram_app = Application.builder().token(TOKEN).updater(None).build()

# ── In-memory conversation state (keyed by chat_id) ───────────────────────────
user_state: dict[int, dict] = {}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Reminder Bot Ready!*\n\n"
        "• /new — create a new reminder\n"
        "• /list — view your reminders",
        parse_mode="Markdown",
    )


async def new_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_chat.id] = {"step": "msg"}
    await update.message.reply_text("📝 Send me the reminder message:")


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = get_reminders(chat_id)
    if not rows:
        await update.message.reply_text("You have no saved reminders.")
        return
    lines = []
    for r in rows:
        rid, message, run_time, repeat_type = r
        lines.append(f"• [{rid}] *{message}* — {run_time} ({repeat_type})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    state = user_state.get(chat_id)
    if not state:
        await update.message.reply_text("Use /new to create a reminder.")
        return

    step = state["step"]

    if step == "msg":
        state["msg"] = text
        state["step"] = "time"
        await update.message.reply_text(
            "⏰ When should I remind you?\n"
            "Format: `YYYY-MM-DD HH:MM` (24-hour, your local time)",
            parse_mode="Markdown",
        )

    elif step == "time":
        # Basic validation
        from datetime import datetime
        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid format. Please use `YYYY-MM-DD HH:MM`.",
                parse_mode="Markdown",
            )
            return

        state["time"] = text
        state["step"] = "repeat"

        keyboard = [[
            InlineKeyboardButton("🔔 One-time", callback_data="once"),
            InlineKeyboardButton("📅 Weekly",   callback_data="weekly"),
            InlineKeyboardButton("🗓 Monthly",  callback_data="monthly"),
        ]]
        await update.message.reply_text(
            "🔁 How often should this repeat?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    state = user_state.pop(chat_id, None)

    if not state or "msg" not in state or "time" not in state:
        await query.edit_message_text("⚠️ Session expired. Use /new to start again.")
        return

    repeat_type = query.data  # "once" | "weekly" | "monthly"
    add_reminder(chat_id, state["msg"], state["time"], repeat_type)

    label = {"once": "one-time", "weekly": "every week", "monthly": "every month"}[repeat_type]
    await query.edit_message_text(
        f"✅ *Reminder saved!*\n\n"
        f"📝 {state['msg']}\n"
        f"⏰ {state['time']}\n"
        f"🔁 Repeats: {label}",
        parse_mode="Markdown",
    )


# ── Register handlers ──────────────────────────────────────────────────────────
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("new",   new_reminder))
telegram_app.add_handler(CommandHandler("list",  list_reminders))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
telegram_app.add_handler(CallbackQueryHandler(button_callback))


# ── FastAPI lifespan (replaces deprecated @app.on_event) ──────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    await telegram_app.initialize()
    webhook_endpoint = f"{WEBHOOK_URL}/webhook"
    await telegram_app.bot.set_webhook(url=webhook_endpoint)
    logger.info(f"Webhook set to {webhook_endpoint}")
    start_scheduler(telegram_app.bot)
    yield
    # Shutdown
    stop_scheduler()
    await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
