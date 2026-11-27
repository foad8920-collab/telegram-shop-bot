"""Bot buttons, bot rules and store-button labels, managed from the
admin panel (like the Yemen bot's BotButton / BotRule tables).

The main menu is an inline keyboard under the welcome message, built
from BotButton rows: menu_name groups buttons into a menu ("القائمة
الرئيسية" is the main menu), sort_order sets the order, full_width
puts a button alone in its row (otherwise two buttons share a row),
and style colours the button - Bot API 9.4: "primary" (blue),
"success" (green), "danger" (red). Only those three colours exist;
anything else renders as the default button. aiogram 3.22.0 passes
style and icon_custom_emoji_id through as extra fields (verified in
the serialized JSON), which is how the Yemen bot's coloured menu works
on the same version. Custom emoji icons need the bot owner's Telegram
Premium (or a Fragment username) to render - same rule as everywhere
else.

BotLabel holds the fixed store buttons (back, main menu, previous,
next, buy, show all): their Arabic/English text and colour live in
the panel too, so every button in the bot is panel-controlled. Product
buttons are dynamic (their text is the product name + price +
quantity from the panel's product rows, their colour is green when in
stock and red when out of stock), and section buttons take their text
from the supplier rows.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

MAIN_MENU_NAME = "القائمة الرئيسية"

BUTTON_STYLES = ("primary", "success", "danger")


def _now():
    return datetime.now(timezone.utc)


class BotButton(Base):
    """One button of a panel-driven menu (same columns as the Yemen bot,
    plus button_text_en for the English menu and full_width for layout).
    """

    __tablename__ = "bot_buttons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    menu_name: Mapped[str] = mapped_column(
        String(60), nullable=False, default=MAIN_MENU_NAME, server_default=MAIN_MENU_NAME
    )
    button_text: Mapped[str] = mapped_column(String(100), nullable=False)
    button_text_en: Mapped[str | None] = mapped_column(String(100), nullable=True)
    callback_data: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    style: Mapped[str | None] = mapped_column(String(20), nullable=True, default="primary")
    icon_custom_emoji_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_width: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __str__(self):
        return self.button_text or ""

    def text_for(self, lang: str) -> str:
        if lang == "en" and (self.button_text_en or "").strip():
            return self.button_text_en.strip()
        return self.button_text


class BotRule(Base):
    """Bot rules shown to users, managed from the admin panel."""

    __tablename__ = "bot_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    text_ar: Mapped[str] = mapped_column(Text, nullable=False)
    text_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(BigInteger, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __str__(self):
        return (self.text_ar or "")[:50]

    def text_for(self, lang: str) -> str:
        if lang == "en" and (self.text_en or "").strip():
            return self.text_en.strip()
        return self.text_ar


# Fixed store buttons, controlled from the panel. A missing row (or a
# renamed key) falls back to the hardcoded default below, so the bot
# never breaks when the table is empty.
DEFAULT_LABELS = {
    "back": ("🔙 رجوع", "🔙 Back", "primary"),
    "main_menu": ("🔙 القائمة الرئيسية", "🔙 Main menu", "primary"),
    "prev": ("⬅️ السابق", "⬅️ Previous", "primary"),
    "next": ("التالي ➡️", "Next ➡️", "primary"),
    "buy": ("🛒 شراء", "🛒 Buy", "success"),
    "all_products": ("📦 عرض الكل", "📦 Show all", "primary"),
}


class BotLabel(Base):
    """One fixed store button's text (Arabic/English) and colour,
    editable from the admin panel. `key` must stay one of the
    DEFAULT_LABELS keys - renaming it just loses the override."""

    __tablename__ = "bot_labels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    text_ar: Mapped[str] = mapped_column(String(100), nullable=False)
    text_en: Mapped[str] = mapped_column(String(100), nullable=False)
    style: Mapped[str] = mapped_column(String(20), nullable=False, default="primary")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __str__(self):
        return self.key or ""


async def load_labels(session) -> dict:
    """{key: (text_ar, text_en, style)} - panel rows override the
    hardcoded defaults; a missing table/row just keeps the default."""
    labels = dict(DEFAULT_LABELS)
    try:
        rows = (await session.execute(select(BotLabel))).scalars().all()
        for row in rows:
            style = (row.style or "primary").strip().lower()
            if style not in BUTTON_STYLES:
                style = "primary"
            labels[row.key] = (row.text_ar, row.text_en, style)
    except Exception:
        pass
    return labels


def label_text(labels: dict, key: str, lang: str):
    """(text, style) for a label key in the requested language."""
    text_ar, text_en, style = labels.get(key, DEFAULT_LABELS[key])
    return (text_ar if lang == "ar" else text_en), style
