"""Panel-driven main menu (Telegram reply keyboard) + button presses.

Nothing about the main menu lives in code: the menu is built from
BotButton rows in the database, so the user adds, edits, deletes,
reorders, renames and recolours every button from the admin panel.
The only code-side piece is the one-time seed below, which runs when
the table is empty - after that the panel owns the menu completely.

A reply-keyboard press arrives as the button's text, so this module
matches the text against the active buttons (Arabic or English) and
dispatches on callback_data. A button with a url and no known action
(for example the support button linking to the support bot) is
answered with that link.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import types
from sqlalchemy import func, select

from .button_models import BUTTON_STYLES, MAIN_MENU_NAME, BotButton, BotRule
from .db import get_session_factory
from .keyboards import TEXTS, get_lang
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


def build_reply_kb(buttons, lang: str) -> types.ReplyKeyboardMarkup:
    """Rows from BotButton rows: a full_width button takes its own row,
    otherwise two buttons share a row, in sort_order."""
    rows: list[list[types.KeyboardButton]] = []
    pending: list[types.KeyboardButton] = []

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
        btn = types.KeyboardButton(text=b.text_for(lang), **kwargs)
        if b.full_width:
            flush()
            rows.append([btn])
        else:
            pending.append(btn)
            if len(pending) == 2:
                flush()
    flush()
    return types.ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=True
    )


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


async def send_main_menu(message: types.Message, lang: str, user=None) -> None:
    """Welcome (Yemen-style) + the panel-driven reply keyboard. Falls
    back to the static welcome + inline menu when the database is
    unavailable. `user` is the Telegram user (needed because
    callback.message.from_user is the bot, not the user)."""
    from .keyboards import main_menu_kb

    tg_user = user or message.from_user
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
            await message.answer(text, reply_markup=build_reply_kb(buttons, lang))
            return
    except Exception:
        logger.exception("SubWorld main menu from panel failed, using inline fallback")
    await message.answer(TEXTS[lang]["welcome"], reply_markup=main_menu_kb(lang))


async def _send_rules(message: types.Message, lang: str) -> None:
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
        await message.answer(
            "القوانين غير متاحة حاليا." if lang == "ar" else "Rules are unavailable right now."
        )
        return
    if not rows:
        await message.answer(
            "لا توجد قوانين بعد — أضفها من لوحة التحكم."
            if lang == "ar"
            else "No rules yet — add them from the admin panel."
        )
        return
    lines = [f"{i}. {r.text_for(lang)}" for i, r in enumerate(rows, 1)]
    await message.answer("\n".join(lines))


async def on_menu_button_text(message: types.Message) -> None:
    """A reply-keyboard press arrives as the button's text: find the
    panel button with that text (Arabic or English) and run its action."""
    text = (message.text or "").strip()
    if not text:
        return
    lang = get_lang(message.from_user.id)
    try:
        async with _session() as session:
            rows = (
                await session.execute(
                    select(BotButton)
                    .where(BotButton.is_active == True)  # noqa: E712
                    .order_by(BotButton.sort_order, BotButton.id)
                )
            ).scalars().all()
    except Exception:
        logger.exception("SubWorld menu button lookup failed")
        return
    button = next(
        (b for b in rows if text in (b.button_text, b.button_text_en or "")),
        None,
    )
    if button is None:
        return
    action = (button.callback_data or "").strip()
    if action == "shop":
        from .catalog import send_store_sections

        await send_store_sections(message, lang)
    elif action == "profile":
        from .catalog import send_balance

        await send_balance(message, lang)
    elif action == "choose_language":
        from .keyboards import CHOOSE_TEXT, choose_kb

        await message.answer(CHOOSE_TEXT, reply_markup=choose_kb())
    elif action == "rules":
        await _send_rules(message, lang)
    elif button.url:
        # Link button (support bot, news channel, ...): send the link.
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=button.text_for(lang), url=button.url
                    )
                ]
            ]
        )
        await message.answer(button.text_for(lang), reply_markup=kb)
    else:
        logger.info("SubWorld menu button %r has no action and no url", action)
