"""Panel-driven main menu (inline buttons under the welcome message).

The menu is an INLINE keyboard attached to the welcome message, so it
sits in the messages area above the input box - exactly like the Yemen
bot's menu. (A reply keyboard is rendered by the client below the
input box, which is why the menu is inline, not a reply keyboard.)

Nothing about the main menu lives in code: the menu is built from
BotButton rows in the database, so the user adds, edits, deletes,
reorders, renames and recolours every button from the admin panel.
The only code-side piece is the one-time seed below, which runs when
the table is empty - after that the panel owns the menu completely.

A press arrives as the button's callback_data (shop / profile /
choose_language / rules / ...), handled by the catalog router; a
button with a url opens that link directly, no handler needed.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import types
from sqlalchemy import func, select

from .button_models import BUTTON_STYLES, MAIN_MENU_NAME, BotButton, BotRule
from .db import get_session_factory
from .keyboards import TEXTS
from .models import Order, SubworldUser

logger = logging.getLogger("subworld.buttons")


def _session():
    return get_session_factory()()


async def load_menu_buttons(session, menu_name: str = MAIN_MENU_NAME):
    rows = (
        await session.execute(
            select(BotButton)
            .where(
                BotButton.menu_name == menu_name,
                BotButton.is_active == True,  # noqa: E712
            )
            .order_by(BotButton.sort_order, BotButton.id)
        )
    ).scalars().all()
    return rows


def build_inline_kb(buttons, lang: str) -> types.InlineKeyboardMarkup:
    """Inline rows from BotButton rows: a full_width button takes its
    own row, otherwise two buttons share a row, in sort_order. A
    button with a url opens the link; otherwise it sends its
    callback_data. Rows with neither are skipped."""
    rows: list[list[types.InlineKeyboardButton]] = []
    pending: list[types.InlineKeyboardButton] = []

    def flush():
        nonlocal pending
        if pending:
            rows.append(pending)
            pending = []

    for b in buttons:
        kwargs: dict = {}
        style = (b.style or "").strip().lower()
        if style in BUTTON_STYLES:
            kwargs["style"] = style
        if (b.icon_custom_emoji_id or "").strip():
            kwargs["icon_custom_emoji_id"] = b.icon_custom_emoji_id.strip()
        url = (b.url or "").strip()
        action = (b.callback_data or "").strip()
        if url:
            btn = types.InlineKeyboardButton(text=b.text_for(lang), url=url, **kwargs)
        elif action:
            btn = types.InlineKeyboardButton(
                text=b.text_for(lang), callback_data=action, **kwargs
            )
        else:
            logger.warning("SubWorld menu button %r has no url or callback_data, skipped", b.button_text)
            continue
        if b.full_width:
            flush()
            rows.append([btn])
        else:
            pending.append(btn)
            if len(pending) == 2:
                flush()
    flush()
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def ensure_default_buttons() -> None:
    """Seed the main menu once, when the table is empty (Yemen-like
    layout: store full-width green, then pairs). The panel can change
    or delete every seeded row afterwards."""
    async with _session() as session:
        count = (
            await session.execute(select(func.count()).select_from(BotButton))
        ).scalar_one()
        if count:
            return
        session.add_all(
            [
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="🛍️ المتجر العام",
                    button_text_en="🛍️ Store",
                    callback_data="shop",
                    style="success",
                    full_width=True,
                    sort_order=1,
                ),
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="💰 رصيدي",
                    button_text_en="💰 Balance",
                    callback_data="profile",
                    style="primary",
                    sort_order=2,
                ),
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="🌐 اللغة",
                    button_text_en="🌐 Language",
                    callback_data="choose_language",
                    style="primary",
                    sort_order=3,
                ),
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="📜 القوانين",
                    button_text_en="📜 Rules",
                    callback_data="rules",
                    style="primary",
                    sort_order=4,
                ),
            ]
        )
        await session.commit()
        logger.info("SubWorld default menu buttons seeded (4 buttons)")


async def _welcome_stats(user_id: int):
    """(balance_usd, purchase_count) from the DB; (None, None) when the
    database is unavailable. Purchases count every order (delivered
    and pending - the balance is already deducted when the order is
    created), like the Yemen bot's all-sources counter."""
    try:
        async with _session() as session:
            user = await session.get(SubworldUser, user_id)
            balance = Decimal(str(user.balance_usd or 0)) if user else Decimal("0")
            count = (
                await session.execute(
                    select(func.count()).select_from(Order).where(Order.user_id == user_id)
                )
            ).scalar_one()
            return balance, count
    except Exception:
        logger.exception("SubWorld welcome stats failed")
        return None, None


def _welcome_text(lang: str, name: str, balance: Decimal, count: int) -> str:
    """Welcome exactly like the Yemen bot's: greeting + balance +
    purchases + 'choose from the menu below' (USD only)."""
    if lang == "en":
        greeting = f"🎉 Welcome, {name}!" if name else "🎉 Welcome!"
        purchases = "1 purchase" if count == 1 else f"{count} purchases"
        return (
            f"{greeting}\n\n"
            f"💎 Balance: ${balance:.2f}\n"
            f"🛍️ Purchases: {purchases}\n\n"
            "Choose from the menu below:"
        )
    greeting = f"🎉 أهلاً بك! {name}" if name else "🎉 أهلاً بك!"
    if count == 1:
        purchases = "مشترى واحد"
    elif count == 2:
        purchases = "مشتريان"
    else:
        purchases = str(count)
    return (
        f"{greeting}\n\n"
        f"💎 رصيدك: ${balance:.2f}\n"
        f"🛍️ مشترياتك: {purchases}\n\n"
        "اختر من القائمة أدناه:"
    )


async def _main_menu_content(lang: str, user):
    """(welcome text, inline keyboard) for the main menu. Falls back
    to the static welcome + built-in inline menu when the database is
    unavailable or the panel has no buttons. `user` is the Telegram
    user (needed because callback.message.from_user is the bot)."""
    from .keyboards import main_menu_kb

    tg_user = user
    name = ""
    if tg_user is not None:
        name = (tg_user.first_name or tg_user.username or "").strip()
    try:
        async with _session() as session:
            buttons = await load_menu_buttons(session)
        if buttons:
            balance, count = await _welcome_stats(tg_user.id)
            if balance is None:
                text = TEXTS[lang]["welcome"]
            else:
                text = _welcome_text(lang, name, balance, count)
            return text, build_inline_kb(buttons, lang)
    except Exception:
        logger.exception("SubWorld main menu from panel failed, using inline fallback")
    return TEXTS[lang]["welcome"], main_menu_kb(lang)


async def send_main_menu(message: types.Message, lang: str, user=None) -> None:
    """Welcome (Yemen-style) + the panel-driven inline menu, as a new
    message (used by /start).

    The welcome is sent first carrying ReplyKeyboardRemove, which
    clears the old reply keyboard left by the pre-v2 menu (a reply
    keyboard stays docked below the input box until the bot removes
    it); the inline menu is then attached to the same message, so the
    chat gets exactly one message and no leftover keyboard."""
    tg_user = user or message.from_user
    text, kb = await _main_menu_content(lang, tg_user)
    sent = await message.answer(text, reply_markup=types.ReplyKeyboardRemove())
    try:
        await sent.edit_reply_markup(reply_markup=kb)
    except Exception:
        logger.exception("SubWorld attach inline menu failed, sending menu again")
        await message.answer(text, reply_markup=kb)


async def edit_main_menu(callback: types.CallbackQuery, lang: str) -> None:
    """Turn the current message back into the welcome + inline menu
    (used by 'back to main menu' and after choosing a language)."""
    from aiogram.exceptions import TelegramBadRequest

    text, kb = await _main_menu_content(lang, callback.from_user)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
        logger.debug("SubWorld main-menu edit skipped: message not modified")


async def _send_rules(callback: types.CallbackQuery, lang: str) -> None:
    """Rules as an edit of the current message, with a back button to
    the main menu."""
    try:
        async with _session() as session:
            rows = (
                await session.execute(
                    select(BotRule)
                    .where(BotRule.is_active == True)  # noqa: E712
                    .order_by(BotRule.sort_order, BotRule.id)
                )
            ).scalars().all()
    except Exception:
        logger.exception("SubWorld rules read failed")
        await callback.message.edit_text(
            "القوانين غير متاحة حاليا." if lang == "ar" else "Rules are unavailable right now.",
            reply_markup=_rules_back_kb(lang),
        )
        await callback.answer()
        return
    if not rows:
        text = (
            "لا توجد قوانين بعد — أضفها من لوحة التحكم."
            if lang == "ar"
            else "No rules yet — add them from the admin panel."
        )
    else:
        text = "\n".join(f"{i}. {r.text_for(lang)}" for i, r in enumerate(rows, 1))
    await callback.message.edit_text(text, reply_markup=_rules_back_kb(lang))
    await callback.answer()


def _rules_back_kb(lang: str) -> types.InlineKeyboardMarkup:
    label = "🔙 القائمة الرئيسية" if lang == "ar" else "🔙 Main menu"
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=label, callback_data="menu:main")]
        ]
    )

