import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from db import get_due_reminders, mark_sent, reschedule

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _send_due_reminders(bot: Bot):
    due = get_due_reminders()
    for reminder_id, chat_id, message, run_time, repeat_type in due:
        try:
            await bot.send_message(chat_id=chat_id, text=f"🔔 *Reminder*\n\n{message}", parse_mode="Markdown")
            mark_sent(reminder_id)
            reschedule(reminder_id, repeat_type)
            logger.info(f"Sent reminder {reminder_id} to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send reminder {reminder_id}: {e}")


def start_scheduler(bot: Bot):
    global _scheduler
    _scheduler = AsyncIOScheduler()
    # Check every minute for due reminders
    _scheduler.add_job(_send_due_reminders, "interval", minutes=1, args=[bot])
    _scheduler.start()
    logger.info("Scheduler started (checking every 60s)")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
