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

def _fmt_price(value) -> str:
    if value is None:
        return ""
    try:
        return f"${value:.2f}"
    except Exception:
        return f"${value}"


def product_updates_keyboard(supplier_id=None, product_id=None):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    if supplier_id is not None and product_id is not None:
        url = f"https://t.me/YemenSubscriptionsBot?start=sv_{supplier_id}_{product_id}"
    else:
        url = "https://t.me/YemenSubscriptionsBot"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=" اشترِ الآن", url=url, style="success"))
    return kb.as_markup()


async def format_supplier_update(ev: dict) -> str:
    import html as _html
    from bot.database.methods.transactions import format_price_arabic_async as _fpa
    name = _html.escape(str(ev.get("name") or "منتج"), quote=False)
    kind = ev.get("kind")
    price_new = ev.get("price_new")
    stock_new = ev.get("stock_new")
    if kind == "new":
        header = " <b>منتج جديد</b>"
    elif kind == "out":
        header = " <b>نفد من المخزون</b>"
    elif kind == "back":
        header = " <b>عاد للمخزون</b>"
    elif kind == "low_stock":
        header = " <b>على وشك النفاذ</b>"
    else:
        price_old = ev.get("price_old")
        if price_old is not None and price_new is not None and price_new > price_old:
            header = " <b>ارتفع السعر</b>"
        elif price_old is not None and price_new is not None and price_new < price_old:
            header = " <b>انخفض السعر</b>"
        else:
            header = " <b>تعديل في المنتج</b>"
    lines = [header, f" <b>{name}</b>"]
    if price_new is not None:
        try:
            lines.append(await _fpa(price_new or 0))
        except Exception:
            lines.append(f" السعر: <b>{price_new}</b>")
    if stock_new is not None:
        lines.append(f" الكمية المتبقية: <b>{stock_new}</b>")
    if stock_new is not None and 0 < stock_new <= 5 and kind != "out":
        lines.append(f" على وشك النفاذ! تبقى <b>{stock_new}</b> فقط")
    return chr(10).join(lines)
