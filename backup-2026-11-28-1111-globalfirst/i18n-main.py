from __future__ import annotations
from contextvars import ContextVar
from functools import lru_cache
from html import escape as _html_escape
from typing import Any
import json
from pathlib import Path

from bot.misc import EnvKeys

LANG_DIR = Path(__file__).parent / "languages"
DEFAULT_LOCALE = "ar"
SUPPORTED_LOCALES = ("ar", "en", "ru")

_current_locale: ContextVar[str] = ContextVar("current_locale", default=DEFAULT_LOCALE)
_USER_LANG_CACHE: dict[int, str] = {}


def load_translations() -> dict[str, dict[str, str]]:
    translations: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LOCALES:
        file = LANG_DIR / f"{lang}.json"
        if file.is_file():
            with open(file, encoding="utf-8") as f:
                translations[lang] = json.load(f)
    return translations


TRANSLATIONS = load_translations()
from bot.logger_mesh import logger


def esc(value: Any) -> str:
    """Escape a value for interpolation into a message."""
    return _html_escape("" if value is None else str(value), quote=False)


@lru_cache(maxsize=1)
def get_locale() -> str:
    loc = EnvKeys.BOT_LOCALE.lower().strip()
    return loc if loc in TRANSLATIONS else DEFAULT_LOCALE


def get_current_locale() -> str:
    """Return the active locale for the current async task/context."""
    loc = _current_locale.get()
    if loc in SUPPORTED_LOCALES:
        return loc
    return get_locale()


def set_current_locale(locale: str) -> None:
    """Set the active locale for the current async task/context."""
    norm = (locale or "").lower().strip()[:2]
    if norm in SUPPORTED_LOCALES:
        _current_locale.set(norm)
    else:
        _current_locale.set(DEFAULT_LOCALE)


def get_user_locale(user_id: int) -> str:
    """Get the cached language for a user or default to Arabic."""
    return _USER_LANG_CACHE.get(user_id, DEFAULT_LOCALE)


def set_user_cache_locale(user_id: int, locale: str) -> None:
    """Cache the user's preferred language in memory."""
    norm = (locale or "").lower().strip()[:2]
    if norm in SUPPORTED_LOCALES:
        _USER_LANG_CACHE[user_id] = norm


def localize(key: str, /, *args: Any, locale: str | None = None, **kwargs: Any) -> str:
    """
    Get translation by key.
    Fallback order: explicitly passed locale -> context locale -> DEFAULT_LOCALE -> 'en' -> key itself.
    """
    explicit_locale = locale or kwargs.pop("locale", None)
    loc = explicit_locale or get_current_locale()
    if loc not in TRANSLATIONS:
        loc = DEFAULT_LOCALE

    text = TRANSLATIONS.get(loc, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get(DEFAULT_LOCALE, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get("en", {}).get(key)
    if text is None:
        text = key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to format translation key '{key}' with kwargs {kwargs}: {e}")
    elif args:
        try:
            text = text.format(*args)
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to format translation key '{key}' with args {args}: {e}")

    return str(text)


def localize_for_user(user_id: int, key: str, /, *args: Any, **kwargs: Any) -> str:
    """
    Translate key for a specific user ID based on their saved language.
    """
    user_lang = get_user_locale(user_id)
    return localize(key, *args, locale=user_lang, **kwargs)
