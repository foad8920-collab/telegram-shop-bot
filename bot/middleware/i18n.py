from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    get_user_locale,
    set_current_locale,
    set_user_cache_locale,
)


class I18nMiddleware(BaseMiddleware):
    """
    Middleware that determines the active language for incoming updates
    and sets the request-scoped locale ContextVar.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user and not user.is_bot:
            # 1) Check in-memory cache
            lang = get_user_locale(user.id)
            if lang == DEFAULT_LOCALE:
                # 2) Check database cache
                from bot.database.methods.read import check_user_cached
                user_record = await check_user_cached(user.id)
                if user_record and user_record.get("language_code"):
                    lang_code = str(user_record["language_code"]).lower()[:2]
                    if lang_code in SUPPORTED_LOCALES:
                        lang = lang_code
                        set_user_cache_locale(user.id, lang)
                elif user.language_code:
                    # 3) Fallback to Telegram client language for new user
                    code = str(user.language_code).lower()[:2]
                    if code in SUPPORTED_LOCALES:
                        lang = code
                        set_user_cache_locale(user.id, lang)

            set_current_locale(lang)
            data["locale"] = lang
        else:
            set_current_locale(DEFAULT_LOCALE)
            data["locale"] = DEFAULT_LOCALE

        return await handler(event, data)
