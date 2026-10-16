"""Middleware لتسجيل كل نشاط المستخدم تلقائيًا."""

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.database.methods.activity import log_activity_bg

logger = logging.getLogger(__name__)


# ============================================================
#  Cache بسيط في الذاكرة (60 ثانية)
# ============================================================
class _TTLCache:
    def __init__(self, ttl: int = 60):
        self._data: dict = {}
        self._ttl = ttl

    def get(self, key):
        item = self._data.get(key)
        if not item:
            return None
        value, expires = item
        if time.time() > expires:
            del self._data[key]
            return None
        return value

    def set(self, key, value):
        self._data[key] = (value, time.time() + self._ttl)


_supplier_cache = _TTLCache(ttl=120)   # 2 دقيقة
_product_cache  = _TTLCache(ttl=120)
_local_sub_cache = _TTLCache(ttl=120)


# ============================================================
#  قاعدة: أزرار ثابتة
# ============================================================
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
    "shop_search": "البحث في المتجر",
    "noop": "(بدون إجراء)",
    "sb_insufficient": "رصيد غير كافٍ",
}


# ============================================================
#  قراءة الاسم من DB (مع cache)
# ============================================================
async def _get_supplier_name(sid: int) -> str:
    cached = _supplier_cache.get(sid)
    if cached:
        return cached

    try:
        from bot.database.main import Database
        from bot.database.models.main import Supplier
        async with Database().session() as s:
            sup = await s.get(Supplier, sid)
            if sup:
                name = sup.display_name or sup.name or f"مورد {sid}"
            else:
                name = f"مورد {sid}"
    except Exception as e:
        logger.debug("supplier lookup failed: %s", e)
        name = f"مورد {sid}"

    _supplier_cache.set(sid, name)
    return name


async def _get_product_name(pid: int) -> str:
    cached = _product_cache.get(pid)
    if cached:
        return cached

    try:
        from bot.database.main import Database
        from bot.database.models.main import SupplierProduct
        async with Database().session() as s:
            prod = await s.get(SupplierProduct, pid)
            if prod:
                name = prod.name or f"منتج {pid}"
            else:
                name = f"منتج {pid}"
    except Exception as e:
        logger.debug("product lookup failed: %s", e)
        name = f"منتج {pid}"

    _product_cache.set(pid, name)
    return name


async def _get_local_sub_name(sid: int) -> str:
    cached = _local_sub_cache.get(sid)
    if cached:
        return cached

    try:
        from bot.database.main import Database
        from bot.database.models.main import LocalSubscription
        async with Database().session() as s:
            sub = await s.get(LocalSubscription, sid)
            if sub:
                name = sub.name or f"اشتراك {sid}"
            else:
                name = f"اشتراك {sid}"
    except Exception as e:
        logger.debug("local sub lookup failed: %s", e)
        name = f"اشتراك {sid}"

    _local_sub_cache.set(sid, name)
    return name


# ============================================================
#  وصف الـ callback (async)
# ============================================================
async def _describe_callback(data: str) -> tuple[str, str, str]:
    """يرجع (action_type, target_ar, target_type)."""
    if not data:
        return ("callback", "غير معروف", "callback")

    # أزرار ثابتة
    if data in CALLBACK_LABELS:
        return ("menu_open", CALLBACK_LABELS[data], "button")

    # --- موردين ---
    if data.startswith("shop_supplier_"):
        try:
            sid = int(data.split("_")[-1])
            name = await _get_supplier_name(sid)
            return ("menu_open", f"متجر {name}", "supplier")
        except (ValueError, IndexError):
            pass

    # --- فئة مورد ---
    if data.startswith("sv_cat:"):
        try:
            parts = data.split(":", 2)
            sid = int(parts[1])
            cat = parts[2] if len(parts) > 2 else "?"
            name = await _get_supplier_name(sid)
            return ("category_view", f"فئة {cat}  مورد {name}", "category")
        except (ValueError, IndexError):
            pass

    # --- صفحة فئات مورد ---
    if data.startswith("sv_prods_page:"):
        try:
            parts = data.split(":", 3)
            sid = int(parts[1])
            cat = parts[2] if len(parts) > 2 else "?"
            name = await _get_supplier_name(sid)
            return ("category_view", f"تصفح {cat}  مورد {name}", "category")
        except (ValueError, IndexError):
            pass

    # --- منتج مورد ---
    if data.startswith("sv_prod:"):
        try:
            parts = data.split(":", 2)
            pid = int(parts[2])
            prod_name = await _get_product_name(pid)
            return ("product_view", prod_name, "product")
        except (ValueError, IndexError):
            pass

    # --- شراء مورد ---
    if data.startswith("sv_buy:"):
        return ("buy_start", "بدء شراء منتج مورد", "buy")

    # --- اختيار كمية ---
    if data.startswith("sb_qty:") or data.startswith("sb_qty_manual:"):
        return ("buy_start", "اختيار الكمية", "buy")

    # --- اختيار طريقة الدفع ---
    if data.startswith("sb_pay_balance:"):
        return ("payment_start", "دفع من رصيد البوت", "payment")
    if data.startswith("sb_pay:"):
        return ("payment_start", "اختيار طريقة الدفع", "payment")

    # --- اشتراك محلي ---
    if data.startswith("local_sub:"):
        try:
            sid = int(data.split(":")[1])
            name = await _get_local_sub_name(sid)
            return ("product_view", name, "product")
        except (ValueError, IndexError):
            pass
    if data.startswith("local_buy:"):
        return ("buy_start", "بدء شراء اشتراك محلي", "buy")

    # --- إحالات ---
    if data.startswith("referral_earnings_") or data.startswith("ref-earn_"):
        return ("referral_click", "عرض أرباح إحالة", "referral")
    if data.startswith("all_earnings_page_"):
        return ("referral_click", "تصفح كل الأرباح", "referral")

    # --- تصفح الفئات عام ---
    if data.startswith("categories-page_"):
        return ("category_view", "تصفح الفئات", "category")

    # --- الدفع اليدوي (للمشرفين) ---
    if data.startswith("pay_"):
        return ("payment_done", "الدفع اليدوي", "payment")

    # --- fallback ---
    return ("callback", data[:60], "callback")


# ============================================================
#  الـ Middleware الرئيسي
# ============================================================
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
                    action_type, target, target_type = await _describe_callback(cb_data)

                    log_activity_bg(
                        user_id, action_type,
                        username=username, first_name=first_name,
                        target=target, target_type=target_type,
                        details={"callback_data": cb_data[:200]},
                    )
        except Exception as e:
            logger.exception("ActivityLoggerMiddleware error: %s", e)

        return await handler(event, data)
