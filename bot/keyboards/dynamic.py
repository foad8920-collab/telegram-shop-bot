"""Dynamic keyboards loaded from bot_buttons table."""
from __future__ import annotations

import time
import logging
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import BotButton
from bot.i18n import localize


logger = logging.getLogger(__name__)


_CACHE_TTL = 60.0
_cache: dict[str, tuple[float, list]] = {}


def invalidate_button_cache(menu_name: Optional[str] = None) -> None:
    """Clear the in-memory button cache.

    Called when admin edits buttons so changes apply immediately.
    If menu_name is None, clears everything.
    """
    global _cache
    if menu_name is None:
        _cache.clear()
    else:
        _cache.pop(menu_name, None)


def _get_user_locale_safe(fallback: str = "ar") -> str:
    """Best-effort fetch of the current locale (never raises)."""
    try:
        from bot.i18n import get_current_locale
        loc = get_current_locale()
        if loc:
            return str(loc)
    except Exception:
        pass
    return fallback


async def load_menu_buttons(menu_name: str, *, force: bool = False) -> list[BotButton]:
    """Load active buttons for a given menu (cached for 60s)."""
    now = time.monotonic()

    if not force:
        cached = _cache.get(menu_name)
        if cached is not None and now - cached[0] < _CACHE_TTL:
            return cached[1]

    try:
        async with Database().session() as session:
            result = await session.execute(
                select(BotButton)
                .where(
                    BotButton.menu_name == menu_name,
                    BotButton.is_active == True,  # noqa: E712
                )
                .order_by(BotButton.sort_order, BotButton.id)
            )
            items = list(result.scalars().all())
    except Exception as e:
        logger.warning("load_menu_buttons failed for %r: %s", menu_name, e)
        return []

    _cache[menu_name] = (now, items)
    return items


def _resolve_text(
    button: BotButton,
    locale: str,
    i18n_key: Optional[str],
    i18n_kwargs: Optional[dict] = None,
) -> str:
    """Choose button text based on locale.

    Priority:
      1. If i18n_key provided AND localize returns a translation  use it.
      2. If CALLBACK_TO_I18N has a mapping for this callback  use that.
      3. Fallback: DB text (button_text).
    """
    kwargs = i18n_kwargs or {}

    # 1) explicit i18n_key
    if i18n_key:
        try:
            result = localize(i18n_key, **kwargs)
            if result and not result.startswith("["):
                return result
        except Exception:
            pass

    # 2) central mapping
    key = CALLBACK_TO_I18N.get(button.callback_data)
    if key:
        try:
            result = localize(key, **kwargs)
            if result and not result.startswith("["):
                return result
        except Exception:
            pass

    # 3) fallback to DB text
    return button.button_text or "-"


def build_dynamic_keyboard(
    buttons: list[BotButton],
    locale: str = "ar",
    i18n_keys: Optional[dict[str, str]] = None,
    i18n_kwargs: Optional[dict[str, dict]] = None,
    per_row: int = 2,
    skip_callbacks: Optional[set[str]] = None,
) -> InlineKeyboardMarkup:
    """Build a keyboard from a list of BotButton rows.

    Args:
        buttons: ordered list of BotButton from DB.
        locale: user locale ("ar", "en", "ru").
        i18n_keys: optional mapping {callback_data: i18n_key} for EN/RU.
        i18n_kwargs: optional mapping {callback_data: kwargs} for i18n.
        per_row: how many buttons per row (default 2).
        skip_callbacks: set of callback_data to skip.
    """
    if not buttons:
        return InlineKeyboardMarkup(inline_keyboard=[])

    i18n_keys = i18n_keys or {}
    i18n_kwargs = i18n_kwargs or {}
    skip = skip_callbacks or set()

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    # Buttons that should always take a full row
    FULL_ROW_CALLBACKS = {"back_to_menu"}

    def _is_full_row(cb: str) -> bool:
        if not cb:
            return False
        if cb in FULL_ROW_CALLBACKS:
            return True
        if cb.startswith("back:"):
            return True
        return False

    for btn in buttons:
        if btn.callback_data in skip:
            continue

        text = _resolve_text(
            btn,
            locale,
            i18n_keys.get(btn.callback_data),
            i18n_kwargs.get(btn.callback_data),
        )
        style = (btn.style or "primary").lower()
        if style not in {"success", "primary", "danger"}:
            style = "primary"

        button = InlineKeyboardButton(
            text=text,
            callback_data=btn.callback_data,
            url=btn.url or None,
            style=style,
        )

        # Full-row buttons: flush current row, then add as single-row
        if _is_full_row(btn.callback_data):
            if current_row:
                rows.append(current_row)
                current_row = []
            rows.append([button])
            continue

        current_row.append(button)

        if len(current_row) >= per_row:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_single_column_keyboard(
    buttons: list[BotButton],
    locale: str = "ar",
    i18n_keys: Optional[dict[str, str]] = None,
    skip_callbacks: Optional[set[str]] = None,
) -> InlineKeyboardMarkup:
    """Same as build_dynamic_keyboard but one button per row (1-column)."""
    return build_dynamic_keyboard(
        buttons, locale=locale, i18n_keys=i18n_keys,
        per_row=1, skip_callbacks=skip_callbacks,
    )


# ============================================================
#  Central mapping: callback_data -> i18n key
# ============================================================
# Populated based on the current bot_buttons table.
# When a callback has no i18n key, we fall back to the DB text.
CALLBACK_TO_I18N: dict[str, str] = {
    # --- Main menu ---
    "shop": "btn.shop",
    "shop_search": "btn.search",
    "profile": "btn.profile",
    "rules": "btn.rules",
    "choose_language": "btn.language",
    "support_ticket": "btn.support",
    "console": "btn.admin_menu",
    "back_to_menu": "btn.back",
    "back:profile": "btn.back",
    "back:admin": "btn.back",
    "back:shop_mgmt": "btn.back",
    "back:payments": "btn.back",
    "back:lang": "btn.back",

    # --- Profile ---
    "replenish_balance": "btn.replenish",
    "cart": "btn.cart_empty",
    "referral_system": "btn.referral",
    "bought_items": "btn.purchased",
    "operation_history": "btn.operation_history",
    "redeem_promo": "btn.redeem_promo",

    # --- Payment options ---
    "pay_cryptopay": "btn.pay.crypto",
    "pay_stars": "btn.pay.stars",
    "pay_fiat": "btn.pay.tg",

    # --- Admin panel ---
    "shop_management": "btn.admin.shop_management",
    "goods_management": "btn.admin.goods_management",
    "categories_management": "btn.admin.categories_management",
    "promo_mgmt": "btn.admin.promo_mgmt",
    "user_management": "btn.admin.user_management",
    "role_mgmt": "btn.admin.role_mgmt",
    "send_message": "btn.admin.send_message",
    "toggle_maintenance": "btn.admin.toggle_maintenance",
    "stackvault_test": "btn.admin.stackvault_test",

    # --- Admin stats ---
    "admin_stats": "btn.admin.admin_stats",
    "admin_logs": "btn.admin.admin_logs",
    "admin_users_view": "btn.admin.admin_users_view",
    "admin_search_item": "btn.admin.admin_search_item",

    # --- Languages ---
    "set_lang:ar": "btn.lang.ar",
    "set_lang:en": "btn.lang.en",
    "set_lang:ru": "btn.lang.ru",
}

