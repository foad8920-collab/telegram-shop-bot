"""Post product updates to the dedicated updates channel, never to support.

The sender never raises: a failed post is logged and returns False, so a
sync or an admin action can never crash because the channel is unavailable.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.misc import EnvKeys

logger = logging.getLogger(__name__)


def parse_updates_channel_username() -> str | None:
    """Extract the updates channel username from UPDATES_CHANNEL_URL."""
    channel_url = EnvKeys.UPDATES_CHANNEL_URL or ""
    parsed = urlparse(channel_url)
    return (
        parsed.path.lstrip("/")
        if parsed.path
        else channel_url.replace("https://t.me/", "").replace("t.me/", "").lstrip("@")
    ) or None


def updates_chat_id() -> int | str | None:
    """Resolve the updates channel address for sending.

    UPDATES_CHANNEL_ID wins when set (private channels have no username),
    otherwise fall back to @username for a public channel.
    """
    raw = EnvKeys.UPDATES_CHANNEL_ID
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("UPDATES_CHANNEL_ID is not a valid chat id; falling back to username")
    username = parse_updates_channel_username()
    return f"@{username}" if username else None


async def send_update(bot, text: str, reply_markup=None) -> bool:
    """Send one update post to the updates channel. Returns False on any failure."""
    chat_id = updates_chat_id()
    if chat_id is None:
        logger.warning("Updates channel is not configured; skipping post")
        return False
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("Failed to post to updates channel: %s", e)
        return False
    except Exception as e:
        logger.warning("Failed to post to updates channel: %s", e)
        return False