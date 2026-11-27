"""SubWorldStoreBot - the main bot (standalone package).

No imports from the Yemen bot package, so the Yemen bot can be retired
later without touching this code. v1: /start with Arabic/English choice
only. No database yet.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

logger = logging.getLogger("subworld")

USER_LANG: dict[int, str] = {}

CHOOSE_TEXT = (
    "أهلا بك في SubWorld - عالم الاشتراكات الرقمية\n"
    "Welcome to SubWorld - Digital Subscriptions World\n"
    "اختر لغتك / Choose your language:"
)

TEXTS = {
    "ar": {
        "welcome": (
            "أهلا بك في SubWorld - عالم الاشتراكات الرقمية\n"
            "المتجر قيد البناء، وسنضيف المنتجات قريبا."
        ),
    },
    "en": {
        "welcome": (
            "Welcome to SubWorld - Digital Subscriptions World\n"
            "The store is under construction, products coming soon."
        ),
    },
}


def choose_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="العربية", callback_data="lang:ar"),
                types.InlineKeyboardButton(text="English", callback_data="lang:en"),
            ]
        ]
    )


def switch_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="تغيير اللغة / Change language",
                    callback_data="lang:choose",
                )
            ]
        ]
    )


async def cmd_start(message: types.Message) -> None:
    lang = USER_LANG.get(message.from_user.id)
    if lang in TEXTS:
        await message.answer(TEXTS[lang]["welcome"], reply_markup=switch_kb())
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
    await callback.message.edit_text(TEXTS[lang]["welcome"], reply_markup=switch_kb())
    await callback.answer()


async def _poll_once(token: str) -> None:
    async with Bot(
        token=token,
        default=DefaultBotProperties(parse_mode="HTML"),
    ) as bot:
        await bot.delete_webhook(drop_pending_updates=False)
        dp = Dispatcher(storage=MemoryStorage())
        dp.message.register(cmd_start, CommandStart())
        dp.callback_query.register(on_lang, F.data.startswith("lang:"))
        info = await bot.get_me()
        logger.info("SubWorld bot started: @%s", info.username)
        # handle_signals=False: the shop bot owns signal handling already.
        await dp.start_polling(bot, handle_signals=False)


async def main() -> None:
    token = os.environ.get("SUBWORLD_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("SUBWORLD_BOT_TOKEN is not set - SubWorld bot disabled.")
        return
    while True:
        try:
            await _poll_once(token)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SubWorld polling failed, retrying in 10s")
            await asyncio.sleep(10)
