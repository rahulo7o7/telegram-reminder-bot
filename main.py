import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from db import init_db, add_reminder, get_reminders, delete_reminder
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")

telegram_app = Application.builder().token(TOKEN).updater(None).build()

user_state: dict[int, dict] = {}

REPEAT_LABELS = {
    "once":    "🔔 One-time",
    "daily":   "📆 Daily",
    "weekly":  "📅 Weekly",
    "monthly": "🗓 Monthly",
}

TIME_PROMPT = (
    "⏰ *When should I remind you?*\n\n"
    "Format: `YYYY-MM-DD HH:MM` (UTC)\n\n"
    "Examples:\n"
    "• `2026-04-11 09:00`\n"
    "• `2026-04-15 18:30`\n\n"
    "_IST = UTC+5:30, so 9 AM IST → `03:30` UTC_"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"👋 Hey *{name}*! I'm your Reminder Bot.\n\n"
        "Commands:\n"
        "• /new — ➕ Create a reminder\n"
        "• /list — 📋 View reminders\n"
        "• /delete — 🗑 Delete a reminder\n"
        "• /cancel — ❌ Cancel current action\n"
        "• /help — ℹ️ Help & tips\n\n"
        "_Reminders fire within 30 seconds of their scheduled time._",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Reminder Bot Help*\n\n"
        "*Creating a reminder:*\n"
        "1. /new\n"
        "2. Type your message\n"
        "3. Send date & time (`YYYY-MM-DD HH:MM` UTC)\n"
        "4. Choose repeat type\n\n"
        "*Repeat options:*\n"
        "🔔 One-time · 📆 Daily · 📅 Weekly · 🗓 Monthly\n\n"
        "*Time zone tip:*\n"
        "Bot uses UTC. IST = UTC+5:30\n"
        "9:00 AM IST = `2026-04-11 03:30`\n\n"
        "/new · /list · /delete · /cancel",
        parse_mode="Markdown",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if user_state.pop(chat_id, None):
        await update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("Nothing to cancel. Use /new to start.")


async def new_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = {"step": "msg"}
    await update.message.reply_text(
        "📝 *New Reminder*\n\nWhat should I remind you about?\n_/cancel to stop._",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = get_reminders(chat_id)
    if not rows:
        await update.message.reply_text("📭 No upcoming reminders. Use /new to create one!")
        return

    lines = ["📋 *Your Reminders*\n"]
    for rid, message, run_time, repeat_type in rows:
        emoji = {"once": "🔔", "daily": "📆", "weekly": "📅", "monthly": "🗓"}.get(repeat_type, "🔔")
        time_str = run_time.strftime("%d %b %Y, %H:%M UTC") if isinstance(run_time, datetime) else str(run_time)
        lines.append(f"{emoji} *[{rid}]* {message}\n    ⏰ _{time_str}_ · {repeat_type}\n")
    lines.append("_/delete to remove one_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = get_reminders(chat_id)
    if not rows:
        await update.message.reply_text("No reminders to delete.")
        return

    keyboard = []
    for rid, message, run_time, repeat_type in rows:
        short = message if len(message) <= 28 else message[:25] + "..."
        keyboard.append([InlineKeyboardButton(f"🗑 [{rid}] {short}", callback_data=f"del_{rid}")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="del_cancel")])

    await update.message.reply_text(
        "🗑 *Which reminder to delete?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    state = user_state.get(chat_id)

    if not state:
        await update.message.reply_text("Use /new to create a reminder, or /help for options.")
        return

    step = state["step"]

    if step == "msg":
        if len(text) > 500:
            await update.message.reply_text("⚠️ Message too long (max 500 chars). Try again:")
            return
        state["msg"] = text
        state["step"] = "time"
        await update.message.reply_text(TIME_PROMPT, parse_mode="Markdown")

    elif step == "time":
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid format. Use `YYYY-MM-DD HH:MM`\nExample: `2026-04-11 09:00`",
                parse_mode="Markdown",
            )
            return

        if dt < datetime.utcnow():
            await update.message.reply_text("⚠️ That's in the past! Send a future date & time:")
            return

        state["time"] = text
        state["step"] = "repeat"

        keyboard = [
            [InlineKeyboardButton("🔔 One-time", callback_data="repeat_once"),
             InlineKeyboardButton("📆 Daily",    callback_data="repeat_daily")],
            [InlineKeyboardButton("📅 Weekly",   callback_data="repeat_weekly"),
             InlineKeyboardButton("🗓 Monthly",  callback_data="repeat_monthly")],
        ]
        await update.message.reply_text(
            "🔁 *How often should this repeat?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    data = query.data

    if data.startswith("del_"):
        if data == "del_cancel":
            await query.edit_message_text("❌ Cancelled.")
            return
        try:
            rid = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            await query.edit_message_text("⚠️ Something went wrong.")
            return
        deleted = delete_reminder(rid, chat_id)
        if deleted:
            await query.edit_message_text(f"✅ Reminder *[{rid}]* deleted.", parse_mode="Markdown")
        else:
            await query.edit_message_text("⚠️ Reminder not found or already deleted.")
        return

    if data.startswith("repeat_"):
        repeat_type = data.split("_", 1)[1]
        state = user_state.pop(chat_id, None)
        if not state or "msg" not in state or "time" not in state:
            await query.edit_message_text("⚠️ Session expired. Use /new to start again.")
            return

        add_reminder(chat_id, state["msg"], state["time"], repeat_type)

        try:
            dt = datetime.strptime(state["time"], "%Y-%m-%d %H:%M")
            nice_time = dt.strftime("%d %b %Y at %H:%M UTC")
        except ValueError:
            nice_time = state["time"]

        label = REPEAT_LABELS.get(repeat_type, repeat_type)
        await query.edit_message_text(
            f"✅ *Reminder saved!*\n\n"
            f"📝 {state['msg']}\n"
            f"⏰ {nice_time}\n"
            f"🔁 {label}\n\n"
            f"_I'll ping you when it's time!_",
            parse_mode="Markdown",
        )


telegram_app.add_handler(CommandHandler("start",  start))
telegram_app.add_handler(CommandHandler("help",   help_cmd))
telegram_app.add_handler(CommandHandler("new",    new_reminder))
telegram_app.add_handler(CommandHandler("list",   list_reminders))
telegram_app.add_handler(CommandHandler("delete", delete_cmd))
telegram_app.add_handler(CommandHandler("cancel", cancel))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
telegram_app.add_handler(CallbackQueryHandler(button_callback))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await telegram_app.initialize()
    webhook_endpoint = f"{WEBHOOK_URL}/webhook"
    await telegram_app.bot.set_webhook(url=webhook_endpoint)
    logger.info(f"Webhook set → {webhook_endpoint}")
    start_scheduler(telegram_app.bot)
    yield
    stop_scheduler()
    await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok", "bot": "running"}


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
