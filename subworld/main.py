"""SubWorldStoreBot - the main bot (standalone package).

No imports from the Yemen bot package, so the Yemen bot can be retired
later without touching this code. Catalog, wallet and supplier sync
live in the subworld package; the Yemen bot's own code is only used
for the supplier API clients (same process, same env keys).
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

from .buttons import (
    edit_main_menu,
    ensure_button_translations,
    ensure_default_buttons,
    ensure_default_labels,
    ensure_rules_seeded,
    send_main_menu,
)
from .catalog import catalog_router
from .keyboards import (
    CHOOSE_TEXT,
    TEXTS,
    USER_LANG,
    choose_kb,
)

logger = logging.getLogger("subworld")


async def cmd_start(message: types.Message) -> None:
    lang = USER_LANG.get(message.from_user.id)
    if lang in TEXTS:
        # Main menu is the panel-driven inline menu (buttons.py).
        await send_main_menu(message, lang, message.from_user)
    else:
        await message.answer(CHOOSE_TEXT, reply_markup=choose_kb())


async def on_lang(callback: types.CallbackQuery) -> None:
    data = callback.data or ""
    if data == "lang:choose":
        await callback.message.edit_text(CHOOSE_TEXT, reply_markup=choose_kb())
        await callback.answer()
        return
    lang = data.split(":", 1)[1] if ":" in data else "ar"
    if lang not in TEXTS:
        lang = "ar"
    USER_LANG[callback.from_user.id] = lang
    # Turn the chooser message into the welcome + inline menu, and show
    # the confirmation as a toast instead of a separate message.
    await edit_main_menu(callback, lang)
    await callback.answer("✅ تم اختيار اللغة." if lang == "ar" else "✅ Language selected.")


async def _sync_loop(bot: Bot) -> None:
    """Sync all suppliers every 30 minutes (first run after 60s)."""
    from .supplier_sync import sync_all_suppliers

    await asyncio.sleep(60)
    while True:
        try:
            await sync_all_suppliers(bot=bot)
        except Exception:
            logger.exception("SubWorld scheduled sync failed")
        await asyncio.sleep(1800)


async def _setup_commands(bot: Bot) -> None:
    """Put /start in the bot's command menu (the Menu button by the
    message box) in Arabic, English and as the default. Never breaks
    startup if Telegram refuses."""
    try:
        await bot.set_my_commands(
            [types.BotCommand(command="start", description="ابدأ البوت")],
            language_code="ar",
        )
        await bot.set_my_commands(
            [types.BotCommand(command="start", description="Start the bot")],
            language_code="en",
        )
        await bot.set_my_commands(
            [types.BotCommand(command="start", description="Start the bot")],
        )
    except Exception:
        logger.warning("SubWorld set_my_commands failed", exc_info=True)


async def _poll_once(token: str, db_ready: bool) -> None:
    async with Bot(
        token=token,
        default=DefaultBotProperties(parse_mode="HTML"),
    ) as bot:
        await bot.delete_webhook(drop_pending_updates=False)
        dp = Dispatcher(storage=MemoryStorage())
        dp.message.register(cmd_start, CommandStart())
        # The main menu is inline: presses arrive as callback_data and
        # are handled by the catalog router (menu:shop / shop, ...).
        dp.callback_query.register(on_lang, F.data.startswith("lang:"))
        dp.include_router(catalog_router)
        info = await bot.get_me()
        logger.info("SubWorld bot started: @%s", info.username)
        await _setup_commands(bot)
        sync_task = None
        if db_ready:
            sync_task = asyncio.create_task(_sync_loop(bot))
        try:
            # handle_signals=False: the shop bot owns signal handling already.
            await dp.start_polling(bot, handle_signals=False)
        finally:
            if sync_task is not None:
                sync_task.cancel()
                try:
                    await sync_task
                except asyncio.CancelledError:
                    pass


async def main() -> None:
    token = os.environ.get("SUBWORLD_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("SUBWORLD_BOT_TOKEN is not set - SubWorld bot disabled.")
        return
    db_ready = False
    if os.environ.get("SUBWORLD_DATABASE_URL", "").strip():
        try:
            from .db import init_db
            from .supplier_sync import (
                ensure_featured_subscriptions_seeded,
                ensure_suppliers_seeded,
            )

            await init_db()
            await ensure_suppliers_seeded()
            await ensure_featured_subscriptions_seeded()
            await ensure_default_buttons()
            await ensure_button_translations()
            await ensure_default_labels()
            await ensure_rules_seeded()
            db_ready = True
            logger.info("SubWorld DB ready.")
        except Exception:
            logger.exception("SubWorld DB init failed - running without catalog")
    else:
        logger.warning("SUBWORLD_DATABASE_URL is not set - catalog disabled.")
    while True:
        try:
            await _poll_once(token, db_ready)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SubWorld polling failed, retrying in 10s")
            await asyncio.sleep(10)
