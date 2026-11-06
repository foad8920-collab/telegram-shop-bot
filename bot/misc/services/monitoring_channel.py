"""Monitoring channel sender - private admin channel for live user activity."""

import asyncio
import datetime
import html
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.misc.env import EnvKeys

logger = logging.getLogger(__name__)


def monitoring_chat_id():
    """Return the monitoring channel id as int, or None when not configured."""
    raw = (EnvKeys.MONITORING_CHANNEL_ID or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("MONITORING_CHANNEL_ID is not a valid integer: %r", raw)
        return None


def format_monitoring_post(*, user_id, username=None, first_name=None,
                           action_ar, target=None, target_type=None):
    """One compact Arabic post for the private monitoring channel."""
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    _hour12 = now.hour % 12 or 12
    _suffix = "صباحا" if now.hour < 12 else "مساء"
    time_s = f"{_hour12}:{now.minute:02d}:{now.second:02d} {_suffix}"
    date_s = now.strftime("%d/%m/%Y")
    name = (first_name or "").strip() or "-"
    user_line = "@" + username if username else "بدون معرف"
    lines = [
        "\U0001F441\uFE0F مراقبة نشاط",
        "\U0001F464 الاسم: " + html.escape(name),
        "\U0001F194 المعرف: <code>" + str(user_id) + "</code>",
        "\U0001F4AC اسم المستخدم: " + html.escape(user_line),
        "\U0001F4CB الإجراء: " + html.escape(action_ar),
    ]
    if target:
        lines.append("\U0001F3AF الهدف: " + html.escape(str(target)))
    lines.append("\U0001F550 الوقت: " + time_s)
    lines.append("\U0001F4C5 التاريخ: " + date_s)
    return chr(10).join(lines)


async def send_monitoring(bot, text) -> bool:
    """Send text to the monitoring channel. Never raises; False on failure."""
    chat_id = monitoring_chat_id()
    if chat_id is None:
        return False
    try:
        await bot.send_message(chat_id, text)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("monitoring send failed: %s", e)
        return False
    except Exception as e:
        logger.warning("monitoring send failed: %s", e)
        return False


def send_monitoring_bg(bot, text) -> None:
    """Schedule a monitoring send in the background (never blocks a handler)."""
    if bot is None:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(send_monitoring(bot, text))
    except Exception as e:
        logger.exception("send_monitoring_bg scheduling failed: %s", e)