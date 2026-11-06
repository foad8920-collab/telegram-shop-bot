"""Chat member updates for the updates channel - join / leave monitoring."""

import logging

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated

from bot.database.methods.activity import ACTION_LABELS, log_activity_bg
from bot.misc.env import EnvKeys
from bot.misc.services.monitoring_channel import format_monitoring_post, send_monitoring_bg

logger = logging.getLogger(__name__)

router = Router()


def _is_updates_channel(chat) -> bool:
    raw = (EnvKeys.UPDATES_CHANNEL_ID or "").strip()
    if raw:
        try:
            if int(raw) == chat.id:
                return True
        except (TypeError, ValueError):
            pass
    username = (getattr(chat, "username", "") or "").lower()
    return username == "yemensubsnews"


@router.chat_member()
async def updates_channel_member_handler(event: ChatMemberUpdated, bot: Bot) -> None:
    """Log and forward join/leave of the updates channel to the monitoring channel."""
    try:
        if not _is_updates_channel(event.chat):
            return
        old = event.old_chat_member.status
        new = event.new_chat_member.status
        user = event.new_chat_member.user
        if old in ("left", "kicked") and new == "member":
            action_type = "channel_join"
        elif old == "member" and new in ("left", "kicked"):
            action_type = "channel_leave"
        else:
            return
        action_ar = ACTION_LABELS.get(action_type, action_type)
        log_activity_bg(
            user.id, action_type,
            username=user.username, first_name=user.first_name,
            target="قناة التحديثات", target_type="channel",
        )
        text = format_monitoring_post(
            user_id=user.id, username=user.username, first_name=user.first_name,
            action_ar=action_ar, target="قناة التحديثات", target_type="channel",
        )
        send_monitoring_bg(bot, text)
    except Exception as e:
        logger.exception("updates_channel_member_handler error: %s", e)