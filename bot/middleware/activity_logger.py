"""Middleware لتسجيل كل نشاط المستخدم تلقائيًا."""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.database.methods.activity import log_activity_bg

logger = logging.getLogger(__name__)


# خرائط لتسمية الأزرار بالعربي
CALLBACK_LABELS = {
    "profile": "الملف الشخصي",
    "referral_system": "نظام الإحالة",
    "shop": "المتجر",
    "shop_local": "الاشتراكات المحلية",
    "back_to_menu": "القائمة الرئيسية",
    "view_referrals": "قائمة إحالاتي",
    "view_all_earnings": "كل الأرباح",
    "claim_rewards": "استلام المكافآت",
    "view_rewards_history": "سجل المكافآت",
    "balance": "الرصيد",
    "topup_balance": "شحن الرصيد",
    "my_purchases": "مشترياتي",
    "support": "الدعم الفني",
    "noop": "(بدون إجراء)",
}


def _describe_callback(data: str) -> tuple[str, str, str]:
    """
    يحلّل callback_data ويرجع (action_type, target, target_type).
    """
    if not data:
        return ("callback", "غير معروف", "callback")

    # حالات خاصة
    if data in CALLBACK_LABELS:
        return ("menu_open", CALLBACK_LABELS[data], "button")

    # الدفع
    if data.startswith("sb_pay_balance:"):
        parts = data.split(":")
        return ("payment_start", f"رصيد البوت  منتج {parts[1] if len(parts) > 1 else '?'}", "payment")
    if data.startswith("sb_pay:"):
        parts = data.split(":")
        return ("payment_start", f"طريقة دفع #{parts[3] if len(parts) > 3 else '?'}", "payment")

    # الشراء من المورد
    if data.startswith("sv_buy:"):
        parts = data.split(":")
        return ("buy_start", f"مورد {parts[1] if len(parts) > 1 else '?'}  منتج {parts[2] if len(parts) > 2 else '?'}", "buy")
    if data.startswith("sb_qty:"):
        return ("buy_start", f"اختيار كمية", "buy")
    if data.startswith("sb_qty_manual:"):
        return ("buy_start", "إدخال كمية يدوية", "buy")

    # عرض منتج مورد
    if data.startswith("sv_prod:"):
        parts = data.split(":")
        return ("product_view", f"مورد {parts[1] if len(parts) > 1 else '?'}  منتج {parts[2] if len(parts) > 2 else '?'}", "product")
    if data.startswith("sv_prods_page:"):
        parts = data.split(":")
        return ("category_view", f"مورد {parts[1] if len(parts) > 1 else '?'}  فئة {parts[2] if len(parts) > 2 else '?'}", "category")

    # عرض اشتراك محلي
    if data.startswith("local_sub:"):
        parts = data.split(":")
        return ("product_view", f"اشتراك محلي #{parts[1] if len(parts) > 1 else '?'}", "product")
    if data.startswith("local_buy:"):
        parts = data.split(":")
        return ("buy_start", f"شراء اشتراك محلي #{parts[1] if len(parts) > 1 else '?'}", "buy")

    # إحالات
    if data.startswith("referral_earnings_") or data.startswith("ref-earn_"):
        return ("referral_click", "عرض أرباح إحالة", "referral")
    if data.startswith("all_earnings_page_"):
        return ("referral_click", "تصفح كل الأرباح", "referral")

    # عرض فئة/منتج عام
    if data.startswith("categories-page_"):
        return ("category_view", "تصفح الفئات", "category")

    # fallback
    short = data[:50]
    return ("callback", short, "callback")


class ActivityLoggerMiddleware(BaseMiddleware):
    """يسجّل كل رسالة وكل ضغطة زر في سجل النشاط."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            user = data.get("event_from_user")
            if user is not None:
                user_id = user.id
                username = user.username
                first_name = user.first_name

                if isinstance(event, Message):
                    text = (event.text or "").strip()
                    if text.startswith("/"):
                        action_type = "command"
                        target = text.split()[0] if text else "/?"
                        target_type = "command"
                    else:
                        action_type = "message"
                        target = text[:100] if text else "(رسالة بدون نص)"
                        target_type = "text"

                    log_activity_bg(
                        user_id, action_type,
                        username=username, first_name=first_name,
                        target=target, target_type=target_type,
                    )

                elif isinstance(event, CallbackQuery):
                    cb_data = event.data or ""
                    action_type, target, target_type = _describe_callback(cb_data)

                    log_activity_bg(
                        user_id, action_type,
                        username=username, first_name=first_name,
                        target=target, target_type=target_type,
                        details={"callback_data": cb_data[:200]},
                    )
        except Exception as e:
            logger.exception("ActivityLoggerMiddleware error: %s", e)

        return await handler(event, data)
