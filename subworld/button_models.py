"""Bot buttons and bot rules, managed from the admin panel (like the
Yemen bot's BotButton / BotRule tables).

The main menu is a Telegram reply keyboard built from BotButton rows:
menu_name groups buttons into a menu ("القائمة الرئيسية" is the main
menu), sort_order sets the order, full_width puts a button alone in
its row (otherwise two buttons share a row), and style colours the
button - Bot API 9.4: "primary" (blue), "success" (green), "danger"
(red). Only those three colours exist; anything else renders as the
default button. aiogram 3.22.0 passes style and icon_custom_emoji_id
through as extra fields (verified in the serialized JSON), which is
how the Yemen bot's coloured menu works on the same version.
Custom emoji icons need the bot owner's Telegram Premium (or a
Fragment username) to render - same rule as everywhere else.
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
