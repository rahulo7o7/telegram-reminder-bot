import os
import logging
import calendar
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

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

IST = timezone(timedelta(hours=5, minutes=30))

REPEAT_LABELS = {
    "once":    "🔔 One-time",
    "daily":   "📆 Daily",
    "weekly":  "📅 Weekly",
    "monthly": "🗓 Monthly",
}

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


def now_ist() -> datetime:
    return datetime.now(IST)


def ist_to_utc_str(ist_dt: datetime) -> str:
    utc_dt = ist_dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%d %H:%M")


def format_ist(run_time_utc) -> str:
    if isinstance(run_time_utc, datetime):
        if run_time_utc.tzinfo is None:
            run_time_utc = run_time_utc.replace(tzinfo=timezone.utc)
        ist_dt = run_time_utc.astimezone(IST)
        return ist_dt.strftime("%d %b %Y, %I:%M %p IST")
    return str(run_time_utc)


def build_year_keyboard() -> InlineKeyboardMarkup:
    current_year = now_ist().year
    years = list(range(current_year, current_year + 10))
    rows = []
    for i in range(0, len(years), 3):
        row = [InlineKeyboardButton(str(y), callback_data=f"dp_year_{y}") for y in years[i:i+3]]
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="dp_cancel")])
    return InlineKeyboardMarkup(rows)


def build_month_keyboard(year: int) -> InlineKeyboardMarkup:
    now = now_ist()
    rows = []
    for i in range(0, 12, 3):
        row = []
        for m in range(i + 1, i + 4):
            disabled = (year == now.year and m < now.month)
            label = MONTHS[m - 1][:3] + ("✗" if disabled else "")
            cb = "dp_ignore" if disabled else f"dp_month_{year}_{m}"
            row.append(InlineKeyboardButton(label, callback_data=cb))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="dp_back_year")])
    return InlineKeyboardMarkup(rows)


def build_day_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    now = now_ist()
    _, days_in_month = calendar.monthrange(year, month)
    rows = []
    row = []
    for d in range(1, days_in_month + 1):
        disabled = (year == now.year and month == now.month and d < now.day)
        label = str(d) + ("✗" if disabled else "")
        cb = "dp_ignore" if disabled else f"dp_day_{year}_{month}_{d}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"dp_back_month_{year}")])
    return InlineKeyboardMarkup(rows)


def build_hour_keyboard(year: int, month: int, day: int) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, 12, 4):
        row = [
            InlineKeyboardButton(f"{h:02d}", callback_data=f"dp_hour_{year}_{month}_{day}_{h}")
            for h in range(i + 1, min(i + 5, 13))
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"dp_back_day_{year}_{month}")])
    return InlineKeyboardMarkup(rows)


def build_minute_keyboard(year: int, month: int, day: int, hour: int) -> InlineKeyboardMarkup:
    minutes = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    rows = []
    for i in range(0, len(minutes), 4):
        row = [
            InlineKeyboardButton(f":{m:02d}", callback_data=f"dp_min_{year}_{month}_{day}_{hour}_{m}")
            for m in minutes[i:i+4]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"dp_back_hour_{year}_{month}_{day}")])
    return InlineKeyboardMarkup(rows)


def build_ampm_keyboard(year: int, month: int, day: int, hour: int, minute: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌅 AM", callback_data=f"dp_ampm_{year}_{month}_{day}_{hour}_{minute}_AM"),
            InlineKeyboardButton("🌆 PM", callback_data=f"dp_ampm_{year}_{month}_{day}_{hour}_{minute}_PM"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"dp_back_min_{year}_{month}_{day}_{hour}")],
    ])


def build_repeat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 One-time", callback_data="repeat_once"),
         InlineKeyboardButton("📆 Daily",    callback_data="repeat_daily")],
        [InlineKeyboardButton("📅 Weekly",   callback_data="repeat_weekly"),
         InlineKeyboardButton("🗓 Monthly",  callback_data="repeat_monthly")],
    ])


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
        "_All times are in IST 🇮🇳_",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Reminder Bot Help*\n\n"
        "*Creating a reminder:*\n"
        "1. /new\n"
        "2. Type your reminder message\n"
        "3. Pick date using the inline buttons\n"
        "4. Pick time (HH:MM AM/PM IST)\n"
        "5. Choose repeat type\n\n"
        "*Repeat options:*\n"
        "🔔 One-time · 📆 Daily · 📅 Weekly · 🗓 Monthly\n\n"
        "_All times are in IST (India Standard Time) 🇮🇳_\n\n"
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
        time_str = format_ist(run_time)
        label = REPEAT_LABELS.get(repeat_type, repeat_type)
        lines.append(f"{emoji} *[{rid}]* {message}\n    ⏰ _{time_str}_ · {label}\n")
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
        state["step"] = "date_year"
        await update.message.reply_text(
            "📅 *Pick a year:*",
            parse_mode="Markdown",
            reply_markup=build_year_keyboard(),
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    data = query.data

    if data == "dp_ignore":
        return

    if data in ("dp_cancel", "del_cancel"):
        user_state.pop(chat_id, None)
        await query.edit_message_text("❌ Cancelled.")
        return

    # Delete flow
    if data.startswith("del_"):
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

    # Back navigation
    if data == "dp_back_year":
        await query.edit_message_text("📅 *Pick a year:*", parse_mode="Markdown",
                                      reply_markup=build_year_keyboard())
        return

    if data.startswith("dp_back_month_"):
        year = int(data.split("_")[3])
        await query.edit_message_text(f"📅 *Pick a month ({year}):*", parse_mode="Markdown",
                                      reply_markup=build_month_keyboard(year))
        return

    if data.startswith("dp_back_day_"):
        parts = data.split("_")
        year, month = int(parts[3]), int(parts[4])
        await query.edit_message_text(f"📅 *Pick a day ({MONTHS[month-1]} {year}):*",
                                      parse_mode="Markdown",
                                      reply_markup=build_day_keyboard(year, month))
        return

    if data.startswith("dp_back_hour_"):
        parts = data.split("_")
        year, month, day = int(parts[3]), int(parts[4]), int(parts[5])
        await query.edit_message_text(
            f"⏰ *Pick an hour* ({day} {MONTHS[month-1]} {year}):",
            parse_mode="Markdown",
            reply_markup=build_hour_keyboard(year, month, day),
        )
        return

    if data.startswith("dp_back_min_"):
        parts = data.split("_")
        year, month, day, hour = int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6])
        await query.edit_message_text(
            f"⏰ *Pick minutes* ({hour:02d}:?? on {day} {MONTHS[month-1]}):",
            parse_mode="Markdown",
            reply_markup=build_minute_keyboard(year, month, day, hour),
        )
        return

    # Forward navigation
    if data.startswith("dp_year_"):
        year = int(data.split("_")[2])
        await query.edit_message_text(f"📅 *Pick a month ({year}):*", parse_mode="Markdown",
                                      reply_markup=build_month_keyboard(year))
        return

    if data.startswith("dp_month_"):
        parts = data.split("_")
        year, month = int(parts[2]), int(parts[3])
        await query.edit_message_text(
            f"📅 *Pick a day ({MONTHS[month-1]} {year}):*",
            parse_mode="Markdown",
            reply_markup=build_day_keyboard(year, month),
        )
        return

    if data.startswith("dp_day_"):
        parts = data.split("_")
        year, month, day = int(parts[2]), int(parts[3]), int(parts[4])
        await query.edit_message_text(
            f"⏰ *Pick an hour* ({day} {MONTHS[month-1]} {year}):\n_12-hour format_",
            parse_mode="Markdown",
            reply_markup=build_hour_keyboard(year, month, day),
        )
        return

    if data.startswith("dp_hour_"):
        parts = data.split("_")
        year, month, day, hour = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
        await query.edit_message_text(
            f"⏰ *Pick minutes* ({hour:02d}:?? on {day} {MONTHS[month-1]}):",
            parse_mode="Markdown",
            reply_markup=build_minute_keyboard(year, month, day, hour),
        )
        return

    if data.startswith("dp_min_"):
        parts = data.split("_")
        year, month, day, hour, minute = (
            int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6])
        )
        await query.edit_message_text(
            f"⏰ *AM or PM?* ({hour:02d}:{minute:02d} on {day} {MONTHS[month-1]} {year})",
            parse_mode="Markdown",
            reply_markup=build_ampm_keyboard(year, month, day, hour, minute),
        )
        return

    if data.startswith("dp_ampm_"):
        parts = data.split("_")
        year, month, day, hour, minute, ampm = (
            int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6]), parts[7]
        )
        if ampm == "AM":
            hour_24 = 0 if hour == 12 else hour
        else:
            hour_24 = hour if hour == 12 else hour + 12

        ist_dt = datetime(year, month, day, hour_24, minute, tzinfo=IST)
        if ist_dt <= now_ist():
            await query.edit_message_text(
                "⚠️ That time is in the past! Use /new to try again."
            )
            user_state.pop(chat_id, None)
            return

        state = user_state.get(chat_id)
        if not state or "msg" not in state:
            await query.edit_message_text("⚠️ Session expired. Use /new to start again.")
            return

        state["time_utc"] = ist_to_utc_str(ist_dt)
        state["time_ist_display"] = ist_dt.strftime("%d %b %Y, %I:%M %p IST")
        state["step"] = "repeat"

        await query.edit_message_text(
            f"✅ *Date & time set:* {state['time_ist_display']}\n\n"
            "🔁 *How often should this repeat?*",
            parse_mode="Markdown",
            reply_markup=build_repeat_keyboard(),
        )
        return

    if data.startswith("repeat_"):
        repeat_type = data.split("_", 1)[1]
        state = user_state.pop(chat_id, None)
        if not state or "msg" not in state or "time_utc" not in state:
            await query.edit_message_text("⚠️ Session expired. Use /new to start again.")
            return

        add_reminder(chat_id, state["msg"], state["time_utc"], repeat_type)
        label = REPEAT_LABELS.get(repeat_type, repeat_type)

        await query.edit_message_text(
            f"✅ *Reminder saved!*\n\n"
            f"📝 {state['msg']}\n"
            f"⏰ {state['time_ist_display']}\n"
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
