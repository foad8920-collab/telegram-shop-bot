"""SubWorld texts and keyboards (Arabic/English)."""
from aiogram import types

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
            "تصفح المتجر واختر اشتراكك، والدفع بالدولار عبر Binance أو الكريبتو أو المحفظة."
        ),
    },
    "en": {
        "welcome": (
            "Welcome to SubWorld - Digital Subscriptions World\n"
            "Browse the store and pick your subscription. Pay in USD via Binance, crypto or wallet."
        ),
    },
}


def get_lang(user_id: int) -> str:
    return USER_LANG.get(user_id, "ar")


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


def main_menu_kb(lang: str) -> types.InlineKeyboardMarkup:
    if lang == "en":
        store, balance, language = "🛍️ Store", "💰 Balance", "🌐 Language"
    else:
        store, balance, language = "🛍️ المتجر", "💰 رصيدي", "🌐 اللغة"
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=store, callback_data="menu:shop")],
            [types.InlineKeyboardButton(text=balance, callback_data="menu:balance")],
            [types.InlineKeyboardButton(text=language, callback_data="menu:lang")],
        ]
    )
