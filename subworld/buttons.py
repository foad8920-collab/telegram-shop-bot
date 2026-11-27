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

from aiogram import types
from sqlalchemy import func, select

from .button_models import BUTTON_STYLES, MAIN_MENU_NAME, BotButton, BotRule
from .db import get_session_factory
from .keyboards import TEXTS, get_lang

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


async def send_main_menu(message: types.Message, lang: str) -> None:
    """Welcome text + the panel-driven reply keyboard. Falls back to
    the inline menu when the database is unavailable."""
    from .keyboards import main_menu_kb

    try:
        async with _session() as session:
            buttons = await load_menu_buttons(session)
        if buttons:
            await message.answer(
                TEXTS[lang]["welcome"], reply_markup=build_reply_kb(buttons, lang)
            )
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
