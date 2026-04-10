import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from db import get_due_reminders, mark_sent, reschedule

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _send_due_reminders(bot: Bot):
    due = get_due_reminders()
    if not due:
        return
    logger.info(f"Scheduler: {len(due)} reminder(s) due")
    for reminder_id, chat_id, message, run_time, repeat_type in due:
        try:
            repeat_label = {"once": "one-time", "weekly": "weekly", "monthly": "monthly"}.get(repeat_type, repeat_type)
            text = (
                f"🔔 *Reminder*\n\n"
                f"📝 {message}\n\n"
                f"_Scheduled: {run_time.strftime('%d %b %Y, %H:%M')}_\n"
                f"_Type: {repeat_label}_"
            )
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            mark_sent(reminder_id)
            reschedule(reminder_id, repeat_type)
            logger.info(f"Sent reminder {reminder_id} to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send reminder {reminder_id}: {e}")


def start_scheduler(bot: Bot):
    """Must be called from within a running asyncio event loop (e.g. FastAPI lifespan)."""
    global _scheduler
    loop = asyncio.get_event_loop()
    _scheduler = AsyncIOScheduler(event_loop=loop)
    _scheduler.add_job(
        _send_due_reminders,
        "interval",
        seconds=30,          # check every 30s so reminders fire promptly
        args=[bot],
        max_instances=1,     # never run two checks in parallel
        misfire_grace_time=60,
    )
    _scheduler.start()
    logger.info("Scheduler started — checking every 30s")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
