"""سجل نشاط المستخدمين - User Activity Logging."""

import asyncio
import datetime
import logging
from typing import Any, Optional

from bot.database import Database
from bot.database.models.main import UserActivity

logger = logging.getLogger(__name__)


# ============================================================
#  Action Types + Arabic Labels
# ============================================================
ACTION_LABELS = {
    "start":            " بدء استخدام البوت",
    "menu_open":        " فتح القائمة الرئيسية",
    "profile_open":     " فتح الملف الشخصي",
    "category_view":    " دخول فئة",
    "product_view":     " عرض منتج",
    "buy_start":        " بدء عملية شراء",
    "buy_success":      " شراء ناجح",
    "buy_fail":         " فشل الشراء",
    "payment_start":    " بدء الدفع",
    "payment_done":     " إتمام الدفع",
    "payment_cancel":   " إلغاء الدفع",
    "referral_click":   " ضغط على رابط إحالة",
    "referral_reward":  " استلام مكافأة إحالة",
    "promo_use":        " استخدام كود خصم",
    "balance_topup":    " شحن رصيد",
    "link_click":       " ضغط على رابط",
    "callback":         " ضغط زر",
    "message":          " رسالة نصية",
    "command":          " أمر",
    "error":            " خطأ",
    "suspicious":       " نشاط مشبوه",
    "admin_action":     " إجراء إداري",
}


# ============================================================
#  Logging Functions
# ============================================================
async def log_activity(
    user_id: int,
    action_type: str,
    *,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    target: Optional[str] = None,
    target_type: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """تسجيل نشاط المستخدم في قاعدة البيانات (ينتظر الإكمال)."""
    try:
        async with Database().session() as session:
            activity = UserActivity(
                user_id=user_id,
                username=username,
                first_name=first_name,
                action_type=action_type,
                action_ar=ACTION_LABELS.get(action_type, action_type),
                target=target,
                target_type=target_type,
                details=details,
            )
            session.add(activity)
            await session.commit()
    except Exception as e:
        logger.exception("log_activity failed: %s", e)


def log_activity_bg(
    user_id: int,
    action_type: str,
    *,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    target: Optional[str] = None,
    target_type: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """تسجيل نشاط في الخلفية (لا ينتظر)."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(log_activity(
            user_id, action_type,
            username=username, first_name=first_name,
            target=target, target_type=target_type,
            details=details,
        ))
    except Exception as e:
        logger.exception("log_activity_bg scheduling failed: %s", e)


# ============================================================
#  Cleanup (حذف السجلات القديمة)
# ============================================================
async def cleanup_old_activities(days: int = 30) -> int:
    """حذف السجلات الأقدم من N يوم. ترجع عدد الصفوف المحذوفة."""
    from sqlalchemy import delete
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with Database().session() as session:
            stmt = delete(UserActivity).where(UserActivity.created_at < cutoff)
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount or 0
            if deleted:
                logger.info("cleanup_old_activities: deleted %d rows (> %d days)", deleted, days)
            return deleted
    except Exception as e:
        logger.exception("cleanup_old_activities failed: %s", e)
        return 0
