import hmac
import logging
import os
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqladmin import Admin, BaseView, ModelView, expose
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from sqlalchemy import select, text

from markupsafe import Markup, escape
from wtforms import SelectField
from wtforms.validators import Optional as WtfOptional
from sqlalchemy import select as sa_select

from bot.misc import EnvKeys
from bot.database.methods.audit import log_audit

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Resolve the real client IP, trusting X-Forwarded-For only from loopback.

    When a reverse proxy on the same host fronts the panel, request.client.host
    is 127.0.0.1; the original client is then the first hop of X-Forwarded-For.
    That header ONLY when the socket peer is loopback, so an external
    client cannot spoof its IP (which would otherwise defeat the default-cred
    guard and the login rate limiter).
    """
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


class LoginRateLimiter:
    """In-memory rate limiter for login attempts by IP."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._attempts: dict[str, list[float]] = {}
        self._last_cleanup: float = time.time()

    def is_blocked(self, ip: str) -> bool:
        if ip not in self._attempts:
            return False
        now = time.time()
        self._attempts[ip] = [t for t in self._attempts[ip] if now - t < self.lockout_seconds]
        return len(self._attempts[ip]) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        now = time.time()
        if now - self._last_cleanup > 600:
            self._attempts = {
                k: [t for t in v if now - t < self.lockout_seconds]
                for k, v in self._attempts.items()
                if any(now - t < self.lockout_seconds for t in v)
            }
            self._last_cleanup = now
        if ip not in self._attempts:
            self._attempts[ip] = []
        self._attempts[ip].append(now)

    def reset(self, ip: str) -> None:
        self._attempts.pop(ip, None)


_login_limiter = LoginRateLimiter()
from bot.database.main import Database
from bot.database.models.main import (
    User, Role, Categories, Goods, ItemValues,
    BoughtGoods, Operations, Payments, ReferralEarnings,
    AuditLog, PromoCodes, CartItems, Reviews, promo_scope_for,
)
from bot.misc.metrics import get_metrics
from bot.misc.caching import get_cache_manager
from bot.database.methods.read import (
    invalidate_user_cache, invalidate_item_cache, invalidate_rating_cache, get_item_name_by_id,
)
from bot.database.methods.cache_utils import safe_create_task
from bot.misc.services.restock_notifier import notify_restock
from bot.middleware.security import invalidate_auth_caches, flush_all_role_caches
from bot.database.models.main import StackVaultPricingSettings, StackVaultPriceOverride
from bot.misc.services import StackVaultClient, StackVaultAPIError
from bot.misc.services.stackvault_pricing import prepare_catalog


# Authentication
class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        ip = _client_ip(request)

        if _login_limiter.is_blocked(ip):
            await log_audit("web_login_blocked", level="WARNING", details=f"ip={ip}", ip_address=ip)
            return False

        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        # Constant-time comparison to avoid leaking credential length/content via
        # response timing. str() guards against a missing form field (None).
        creds_ok = (
            hmac.compare_digest(str(username), str(EnvKeys.ADMIN_USERNAME))
            and hmac.compare_digest(str(password), str(EnvKeys.ADMIN_PASSWORD))
        )
        if creds_ok:
            if (
                username == "admin" and password == "admin"
                and ip not in ("127.0.0.1", "::1", "localhost")
            ):
                await log_audit("web_login_blocked_default_creds", level="WARNING", details=f"ip={ip}", ip_address=ip)
                return False
            request.session.update({"authenticated": True})
            _login_limiter.reset(ip)
            await log_audit("web_login", user_id=None, details=f"user={username}", ip_address=ip)
            return True

        _login_limiter.record_failure(ip)
        await log_audit("web_login_failed", level="WARNING", details=f"user={username}", ip_address=ip)
        return False

    async def logout(self, request: Request) -> bool:
        await log_audit("web_logout", ip_address=_client_ip(request))
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)


def _safe_model_repr(model: Any, max_len: int = 500) -> str:
    """Return a truncated repr that excludes sensitive fields."""
    _sensitive = {"balance", "password", "secret", "token", "value"}
    parts = []
    for col in getattr(model, "__table__", None).columns if hasattr(model, "__table__") else ():
        if col.name in _sensitive:
            continue
        val = getattr(model, col.name, None)
        parts.append(f"{col.name}={val!r}")
    result = f"{type(model).__name__}({', '.join(parts)})"
    return result[:max_len]


_notifier_bot: Any = None


def set_notifier_bot(bot: Any) -> None:
    global _notifier_bot
    _notifier_bot = bot


# Audited base view for mutable models
class AuditModelView(ModelView):
    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        action = f"sqladmin_{'create' if is_created else 'update'}"
        await log_audit(
            action,
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=_client_ip(request),
        )

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await log_audit(
            "sqladmin_delete",
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=_client_ip(request),
        )


# Model Views
class UserAdmin(AuditModelView, model=User):
    column_list = [User.telegram_id, User.balance, User.role_id, User.referral_id,
                   User.registration_date, User.is_blocked]
    column_searchable_list = [User.telegram_id]
    column_sortable_list = [User.telegram_id, User.balance, User.registration_date]
    column_default_sort = (User.registration_date, True)
    form_excluded_columns = [
        User.user_operations, User.user_goods,
        User.referral_earnings_received, User.referral_earnings_generated,
    ]
    name = "مستخدم"
    name_plural = "المستخدمون"
    column_labels = {"telegram_id": "معرّف Telegram", "balance": "الرصيد", "role_id": "الدور", "referral_id": "المعرّف المُحيل", "registration_date": "تاريخ التسجيل", "is_blocked": "محظور"}
    icon = "fa-solid fa-users"

    async def _invalidate(self, model: Any, *, blocked: bool | None = None) -> None:
        # A web edit of balance/role_id/is_blocked would otherwise be served stale
        # from Redis (user/role, up to 600s) and from the middleware's in-memory
        # role cache + blocked set (until restart). Clear both; the blocked set
        # is authoritative per-update, so pass the new block state explicitly.
        tid = getattr(model, "telegram_id", None)
        if tid is not None:
            safe_create_task(invalidate_user_cache(int(tid)))
            invalidate_auth_caches(int(tid), blocked=blocked)

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        await super().after_model_change(data, model, is_created, request)
        await self._invalidate(model, blocked=bool(getattr(model, "is_blocked", False)))

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await super().after_model_delete(model, request)
        await self._invalidate(model, blocked=False)


_PERM_FLAGS = [
    (1,   "USE"),
    (2,   "BROADCAST"),
    (4,   "SETTINGS"),
    (8,   "USERS"),
    (16,  "CATALOG"),
    (32,  "ADMINS"),
    (64,  "OWNER"),
    (128, "STATS"),
    (256, "BALANCE"),
    (512, "PROMOS"),
]


def _format_perms_html(model, name):
    perms = getattr(model, name, 0) or 0
    if not perms:
        return Markup('<span style="color:#999">\u2014</span>')
    badges = []
    for bit, label in _PERM_FLAGS:
        if perms & bit:
            badges.append(
                f'<span style="display:inline-block;background:#e2e8f0;padding:1px 6px;'
                f'border-radius:4px;margin:1px;font-size:12px">{label}</span>'
            )
    raw = f'<span style="color:#999;font-size:11px;margin-left:4px">({perms})</span>'
    return Markup(" ".join(badges) + raw)


class RoleAdmin(AuditModelView, model=Role):
    column_list = [Role.id, Role.name, Role.default, Role.permissions]
    column_details_exclude_list = ["users"]
    form_excluded_columns = [Role.users]
    column_sortable_list = [Role.id, Role.name]
    name = "دور"
    name_plural = "الأدوار والصلاحيات"
    column_labels = {"id": "الرقم", "name": "اسم الدور", "default": "افتراضي", "permissions": "الصلاحيات"}
    icon = "fa-solid fa-shield-halved"
    column_formatters = {"permissions": _format_perms_html}
    column_formatters_detail = {"permissions": _format_perms_html}
    form_args = {
        "permissions": {
            "description": (
                "Bitmask value — sum the flags you need: "
                "USE=1, BROADCAST=2, SETTINGS=4, USERS=8, CATALOG=16, ADMINS=32, "
                "OWNER=64, STATS=128, BALANCE=256, PROMOS=512. "
                "Example: 927 = full Admin, 1023 = all (Owner)."
            ),
        },
    }

    @staticmethod
    async def _flush_role_caches() -> None:
        # A Role's permission bitmask affects every user holding that role, so
        # invalidation cannot be scoped to one id: flush all role caches.
        await flush_all_role_caches()

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        await super().after_model_change(data, model, is_created, request)
        await self._flush_role_caches()

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await super().after_model_delete(model, request)
        await self._flush_role_caches()


class CategoryAdmin(AuditModelView, model=Categories):
    column_list = [Categories.name]
    column_searchable_list = [Categories.name]
    form_excluded_columns = [Categories.items]
    name = "تصنيف"
    name_plural = "التصنيفات"
    column_labels = {"name": "اسم التصنيف"}
    icon = "fa-solid fa-folder"


class GoodsAdmin(AuditModelView, model=Goods):
    column_list = [Goods.id, Goods.name, Goods.price, Goods.sale_percent,
                   Goods.sale_until, Goods.description, Goods.category_id]
    column_searchable_list = [Goods.name]
    column_sortable_list = [Goods.id, Goods.name, Goods.price]
    form_excluded_columns = [Goods.values]
    name = "المنتج"
    name_plural = "المنتجات"
    icon = "fa-solid fa-box"
    column_labels = {
        "id": "الرقم",
        "name": "اسم المنتج",
        "price": "السعر",
        "description": "الوصف",
        "category_id": "التصنيف",
        "sale_percent": "نسبة الخصم",
        "sale_until": "انتهاء الخصم",
        "stackvault_product_id": "معرّف منتج StackVault",
        "supplier_price": "سعر المورد",
        "supplier_stock": "مخزون المورد",
        "supplier_in_stock": "المنتج متوفر لدى المورد",
        "stackvault_enabled": "تفعيل StackVault",
        "stackvault_pricing_mode": "طريقة التسعير",
        "stackvault_pricing_value": "قيمة التسعير",
        "stock_quantity": "الكمية المحلية",
    }
    form_args = {
        "id": {"label": "الرقم"},
        "name": {"label": "اسم المنتج"},
        "price": {"label": "السعر"},
        "description": {"label": "الوصف"},
        "category_id": {"label": "التصنيف"},
        "stackvault_product_id": {"label": "معرّف منتج StackVault"},
        "supplier_price": {"label": "سعر المورد"},
        "supplier_stock": {"label": "مخزون المورد"},
        "supplier_in_stock": {"label": "المنتج متوفر لدى المورد"},
        "stackvault_enabled": {"label": "تفعيل StackVault"},
        "stackvault_pricing_mode": {"label": "طريقة التسعير"},
        "stackvault_pricing_value": {"label": "قيمة التسعير"},
        "stock_quantity": {"label": "الكمية المحلية"},
        "sale_percent": {
            "label": "نسبة الخصم",
            "description": (
                "نسبة الخصم (0-100) أثناء سريان الخصم. اتركها فارغة لتعطيل الخصم."
            ),
        },
        "sale_until": {
            "label": "انتهاء الخصم",
            "description": (
                "وقت انتهاء الخصم (UTC). يطبّق الخصم ما دام الوقت في المستقبل؛ "
                "والوقت المنقضي أو الفارغ يعني عدم وجود خصم فعّال."
            ),
        },
    }

    async def _invalidate(self, model: Any) -> None:
        name = getattr(model, "name", None)
        if name:
            safe_create_task(invalidate_item_cache(name))

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        await super().after_model_change(data, model, is_created, request)
        await self._invalidate(model)

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await super().after_model_delete(model, request)
        await self._invalidate(model)


class ItemValuesAdmin(AuditModelView, model=ItemValues):
    column_list = [ItemValues.id, ItemValues.item_id, ItemValues.value, ItemValues.is_infinity]
    column_searchable_list = [ItemValues.value]
    column_sortable_list = [ItemValues.id, ItemValues.item_id]
    name = "عنصر مخزون"
    name_plural = "عناصر المخزون"
    column_labels = {"id": "الرقم", "item_id": "المنتج", "value": "المحتوى", "is_infinity": "غير محدود"}
    icon = "fa-solid fa-warehouse"

    async def _item_name(self, model: Any) -> str | None:
        item_id = getattr(model, "item_id", None)
        return await get_item_name_by_id(int(item_id)) if item_id is not None else None

    async def _invalidate(self, name: str) -> None:
        safe_create_task(invalidate_item_cache(name))

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        await super().after_model_change(data, model, is_created, request)
        name = await self._item_name(model)
        if not name:
            return
        await self._invalidate(name)

        if is_created and _notifier_bot is not None:
            safe_create_task(notify_restock(_notifier_bot, name))

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await super().after_model_delete(model, request)
        name = await self._item_name(model)
        if name:
            await self._invalidate(name)


class BoughtGoodsAdmin(ModelView, model=BoughtGoods):
    column_list = [BoughtGoods.id, BoughtGoods.item_name, BoughtGoods.value,
                   BoughtGoods.price, BoughtGoods.buyer_id, BoughtGoods.bought_datetime,
                   BoughtGoods.unique_id]
    column_searchable_list = [BoughtGoods.item_name, BoughtGoods.buyer_id, BoughtGoods.unique_id]
    column_sortable_list = [BoughtGoods.id, BoughtGoods.bought_datetime, BoughtGoods.price]
    column_default_sort = (BoughtGoods.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "شراء"
    name_plural = "المشتريات"
    column_labels = {"id": "الرقم", "item_name": "اسم المنتج", "value": "المحتوى", "price": "السعر", "buyer_id": "المشتري", "bought_datetime": "تاريخ الشراء", "unique_id": "المعرّف الفريد"}
    icon = "fa-solid fa-cart-shopping"


class OperationsAdmin(ModelView, model=Operations):
    column_list = [Operations.id, Operations.user_id, Operations.operation_value,
                   Operations.operation_time]
    column_searchable_list = [Operations.user_id]
    column_sortable_list = [Operations.id, Operations.operation_time, Operations.operation_value]
    column_default_sort = (Operations.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "عملية"
    name_plural = "العمليات"
    column_labels = {"id": "الرقم", "user_id": "المستخدم", "operation_value": "قيمة العملية", "operation_time": "وقت العملية"}
    icon = "fa-solid fa-money-bill-transfer"


class PaymentsAdmin(ModelView, model=Payments):
    column_list = [Payments.id, Payments.provider, Payments.external_id, Payments.user_id,
                   Payments.amount, Payments.currency, Payments.status, Payments.created_at]
    column_searchable_list = [Payments.user_id, Payments.external_id, Payments.provider]
    column_sortable_list = [Payments.id, Payments.created_at, Payments.amount, Payments.status]
    column_default_sort = (Payments.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "دفعة"
    name_plural = "المدفوعات"
    column_labels = {"id": "الرقم", "provider": "بوابة الدفع", "external_id": "المعرّف الخارجي", "user_id": "المستخدم", "amount": "المبلغ", "currency": "العملة", "status": "الحالة", "created_at": "تاريخ الإنشاء"}
    icon = "fa-solid fa-credit-card"


class ReferralEarningsAdmin(ModelView, model=ReferralEarnings):
    column_list = [ReferralEarnings.id, ReferralEarnings.referrer_id,
                   ReferralEarnings.referral_id, ReferralEarnings.amount,
                   ReferralEarnings.original_amount, ReferralEarnings.created_at]
    column_searchable_list = [ReferralEarnings.referrer_id, ReferralEarnings.referral_id]
    column_sortable_list = [ReferralEarnings.id, ReferralEarnings.created_at, ReferralEarnings.amount]
    column_default_sort = (ReferralEarnings.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "أرباح إحالة"
    name_plural = "أرباح الإحالات"
    column_labels = {"id": "الرقم", "referrer_id": "المُحيل", "referral_id": "المُحال", "amount": "المبلغ", "original_amount": "المبلغ الأصلي", "created_at": "تاريخ الإنشاء"}
    icon = "fa-solid fa-handshake"


class AuditLogAdmin(ModelView, model=AuditLog):
    column_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.user_id,
                   AuditLog.action, AuditLog.resource_type, AuditLog.resource_id,
                   AuditLog.details, AuditLog.ip_address]
    column_searchable_list = [AuditLog.action, AuditLog.resource_type, AuditLog.details]
    column_sortable_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.action]
    column_default_sort = (AuditLog.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "سجل تدقيق"
    name_plural = "سجل التدقيق"
    column_labels = {"id": "الرقم", "timestamp": "الوقت", "level": "المستوى", "user_id": "المستخدم", "action": "الإجراء", "resource_type": "نوع المورد", "resource_id": "معرّف المورد", "details": "التفاصيل", "ip_address": "عنوان IP"}
    icon = "fa-solid fa-clipboard-list"


def _format_promo_scope_html(model, name):
    """Render scope, flagging a promo whose bound category/item was deleted.
    """
    scope = getattr(model, name, None) or "global"
    dangling = (
        (scope == "category" and getattr(model, "category_id", None) is None)
        or (scope == "item" and getattr(model, "item_id", None) is None)
    )
    if not dangling:
        return Markup(
            f'<span style="display:inline-block;background:#e2e8f0;padding:1px 6px;'
            f'border-radius:4px;font-size:11px">{escape(scope)}</span>'
        )
    return Markup(
        f'<span style="display:inline-block;background:#e2e8f0;padding:1px 6px;'
        f'border-radius:4px;font-size:11px">{escape(scope)}</span> '
        f'<span style="display:inline-block;background:#fed7d7;color:#9b2c2c;'
        f'padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600" '
        f'title="The bound category/item was deleted. This promo now applies to nothing.">'
        f'DANGLING</span>'
    )


class PromoCodeAdmin(AuditModelView, model=PromoCodes):
    column_list = [PromoCodes.id, PromoCodes.code, PromoCodes.discount_type,
                   PromoCodes.discount_value, PromoCodes.scope, PromoCodes.category_id,
                   PromoCodes.item_id, PromoCodes.max_uses, PromoCodes.current_uses,
                   PromoCodes.is_active, PromoCodes.expires_at, PromoCodes.created_at]
    column_searchable_list = [PromoCodes.code]
    column_sortable_list = [PromoCodes.id, PromoCodes.code, PromoCodes.created_at]
    column_default_sort = (PromoCodes.id, True)
    form_columns = [PromoCodes.code, PromoCodes.discount_type, PromoCodes.discount_value,
                    PromoCodes.scope, PromoCodes.max_uses, PromoCodes.expires_at, PromoCodes.is_active]
    form_overrides = {"discount_type": SelectField, "scope": SelectField}
    form_args = {
        "discount_type": {
            "choices": [
                ("percent", "نسبة مئوية (خصم من السعر)"),
                ("fixed", "مبلغ ثابت (خصم من السعر)"),
                ("balance", "شحن الرصيد (إضافة للمستخدم)"),
            ],
            "description": "كيفية تطبيق discount_value.",
        },
        "scope": {
            "choices": [
                ("global", "عام (المتجر بالكامل)"),
                ("category", "تصنيف (اختر تصنيفًا)"),
                ("item", "منتج (اختر منتجًا)"),
            ],
            "description": (
                "Where the promo applies. Must match the binding: 'category' "
                "needs a Category selected, 'item' needs an Item selected, "
                "'global' needs neither. This is what keeps a promo scoped after "
                "its category/item is deleted."
            ),
        },
    }
    column_formatters = {"scope": _format_promo_scope_html}
    column_formatters_detail = {"scope": _format_promo_scope_html}
    name = "كود ترويجي"
    name_plural = "الأكواد الترويجية"
    column_labels = {"id": "الرقم", "code": "الكود", "discount_type": "نوع الخصم", "discount_value": "قيمة الخصم", "scope": "النطاق", "category_id": "التصنيف", "item_id": "المنتج", "max_uses": "الحد الأقصى للاستخدام", "current_uses": "الاستخدامات الحالية", "is_active": "نشط", "expires_at": "تاريخ الانتهاء", "created_at": "تاريخ الإنشاء"}
    icon = "fa-solid fa-tag"

    async def scaffold_form(self, *args, **kwargs):
        """Add Category / Item as dropdowns of real records."""
        Form = await super().scaffold_form(*args, **kwargs)

        async with self.session_maker() as s:
            cats = (await s.execute(
                sa_select(Categories.id, Categories.name).order_by(Categories.name)
            )).all()
            items = (await s.execute(
                sa_select(Goods.id, Goods.name).order_by(Goods.name)
            )).all()

        none_label = "— بلا ربط (عام) —"
        cat_choices = [("", none_label)] + [(str(cid), name) for cid, name in cats]
        item_choices = [("", none_label)] + [(str(gid), name) for gid, name in items]

        def _coerce(v):
            return int(v) if v not in (None, "", "None") else None

        class PromoFormWithBindings(Form):
            category_id = SelectField(
                "التصنيف", choices=cat_choices, coerce=_coerce,
                validators=[WtfOptional()],
                description="Only for scope = category.",
            )
            item_id = SelectField(
                "المنتج", choices=item_choices, coerce=_coerce,
                validators=[WtfOptional()],
                description="Only for scope = item.",
            )

        return PromoFormWithBindings

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        """Validate/normalize a promo before persisting."""
        code = (data.get("code") or "").strip().upper()
        if not code:
            raise ValueError("Code is required.")
        data["code"] = code

        dtype = data.get("discount_type")
        if dtype not in ("percent", "fixed", "balance"):
            raise ValueError("discount_type must be one of: percent, fixed, balance.")

        try:
            dval = Decimal(str(data.get("discount_value")))
        except (InvalidOperation, TypeError):
            raise ValueError("discount_value must be a number.")
        if dval < 0:
            raise ValueError("discount_value must be >= 0.")
        if dtype == "percent" and dval > 100:
            raise ValueError("A percent discount_value must be between 0 and 100.")

        # The SelectFields coerce to int or None; normalize anything else too.
        def _as_id(v):
            if v in (None, "", "None"):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        category_id = _as_id(data.get("category_id"))
        item_id = _as_id(data.get("item_id"))
        data["category_id"] = category_id
        data["item_id"] = item_id

        if category_id is not None and item_id is not None:
            raise ValueError("A promo cannot bind both a category and an item — choose one.")

        scope = (data.get("scope") or "global").strip()
        expected = promo_scope_for(category_id, item_id)
        if scope != expected:
            raise ValueError(
                f"Scope '{scope}' does not match the binding — select '{expected}' "
                f"(or set/clear the matching Category/Item)."
            )


class CartItemsAdmin(ModelView, model=CartItems):
    column_list = [CartItems.id, CartItems.user_id, CartItems.item_id, CartItems.added_at]
    column_searchable_list = [CartItems.user_id, CartItems.item_id]
    column_sortable_list = [CartItems.id, CartItems.added_at]
    column_default_sort = (CartItems.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "عنصر سلة"
    name_plural = "عناصر السلة"
    column_labels = {"id": "الرقم", "user_id": "المستخدم", "item_id": "المنتج", "added_at": "تاريخ الإضافة"}
    icon = "fa-solid fa-cart-plus"



class ReviewsAdmin(AuditModelView, model=Reviews):
    column_list = [Reviews.id, Reviews.user_id, Reviews.item_id,
                   Reviews.rating, Reviews.text, Reviews.created_at]
    column_searchable_list = [Reviews.user_id, Reviews.item_id]
    column_sortable_list = [Reviews.id, Reviews.rating, Reviews.created_at]
    column_default_sort = (Reviews.id, True)
    name = "تقييم"
    name_plural = "التقييمات"
    column_labels = {"id": "الرقم", "user_id": "المستخدم", "item_id": "المنتج", "rating": "التقييم", "text": "النص", "created_at": "تاريخ الإنشاء"}
    icon = "fa-solid fa-star"

    async def _invalidate(self, model: Any) -> None:
        # avg_rating is cached for 600s and keyed by product name, so editing a rating here would otherwise not show up in the bot until it expires.
        item_id = getattr(model, "item_id", None)
        if item_id is None:
            return
        name = await get_item_name_by_id(int(item_id))
        if name:
            safe_create_task(invalidate_rating_cache(name))

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        await super().after_model_change(data, model, is_created, request)
        await self._invalidate(model)

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await super().after_model_delete(model, request)
        await self._invalidate(model)


# Health & Metrics Endpoints
async def health_check(request: Request) -> JSONResponse:
    db_ok = True
    try:
        async with Database().session() as s:
            await s.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Health check database error: {e}")
        db_ok = False

    status_code = 200 if db_ok else 503
    if not request.session.get("authenticated"):
        return JSONResponse(
            {"status": "healthy" if db_ok else "unhealthy"},
            status_code=status_code,
        )

    # Authenticated operators get the full diagnostic view.
    health_status = {
        "status": "healthy" if db_ok else "unhealthy",
        "checks": {"database": "ok" if db_ok else "error"},
    }

    cache = get_cache_manager()
    if cache:
        health_status["checks"]["redis"] = "ok" if cache._healthy else "degraded"
    else:
        health_status["checks"]["redis"] = "not configured"

    metrics = get_metrics()
    if metrics:
        health_status["checks"]["metrics"] = "ok"
        health_status["uptime"] = metrics.get_metrics_summary()["uptime_seconds"]

    return JSONResponse(health_status, status_code=status_code)


async def prometheus_metrics(request: Request) -> PlainTextResponse:
    if not request.session.get("authenticated"):
        return PlainTextResponse("Unauthorized", status_code=401)
    metrics = get_metrics()
    if not metrics:
        return PlainTextResponse("# Metrics not initialized\n", status_code=503)
    return PlainTextResponse(metrics.export_to_prometheus(), media_type="text/plain")


async def metrics_json(request: Request) -> JSONResponse:
    if not request.session.get("authenticated"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    metrics = get_metrics()
    if not metrics:
        return JSONResponse({"error": "Metrics not initialized"}, status_code=503)
    return JSONResponse(metrics.get_metrics_summary(), status_code=200)


async def stackvault_pricing_page(request: Request):
    """StackVault catalog and pricing view for administrators."""
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)

    templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
    products = []
    markup = None
    error = None
    success = None

    if request.method == "POST":
        form = await request.form()
        action = form.get("action")
        if action in ("save_override", "remove_override"):
            try:
                product_id = str(form.get("product_id") or "").strip()
                if not product_id:
                    raise ValueError("معرّف المنتج غير صالح.")
                async with Database().session() as session:
                    override = (await session.execute(
                        select(StackVaultPriceOverride).where(
                            StackVaultPriceOverride.product_id == product_id
                        )
                    )).scalars().first()
                    if action == "remove_override":
                        if override is not None:
                            await session.delete(override)
                        audit_details = f"removed manual_price for product_id={product_id}"
                        audit_action = "stackvault_manual_price_removed"
                    else:
                        try:
                            manual_price = Decimal(str(form.get("manual_price")).strip())
                            if not manual_price.is_finite() or manual_price < 0:
                                raise ValueError
                        except (InvalidOperation, TypeError, ValueError, AttributeError):
                            raise ValueError("السعر اليدوي يجب أن يكون رقمًا صالحًا finite وأكبر من أو يساوي صفرًا.")
                        manual_price = manual_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if override is None:
                            override = StackVaultPriceOverride(
                                product_id=product_id,
                                manual_price=manual_price,
                            )
                            session.add(override)
                            audit_action = "stackvault_manual_price_added"
                        else:
                            override.manual_price = manual_price
                            audit_action = "stackvault_manual_price_updated"
                        audit_details = f"manual_price={manual_price} for product_id={product_id}"
                    await session.commit()
                await log_audit(
                    audit_action,
                    resource_type="StackVaultPriceOverride",
                    resource_id=product_id,
                    details=audit_details,
                    ip_address=_client_ip(request),
                )
                success = "تم تحديث السعر اليدوي بنجاح." if action == "save_override" else "تمت إزالة السعر اليدوي بنجاح."
            except ValueError as exc:
                error = str(exc)
            except Exception:
                logger.exception("Failed to update StackVault manual price")
                error = "تعذر حفظ التغيير. لم يتم تسجيل نجاح العملية."

    try:
        async with StackVaultClient() as client:
            source_products = await client.get_products()
        # Keep incomplete supplier records renderable and compatible with
        # prepare_catalog, which expects an ``id`` key even when it is null.
        catalog_products = [
            {**product, "id": product.get("id")}
            for product in source_products
            if isinstance(product, dict)
        ]
        ids = [str(product["id"]) for product in catalog_products if product.get("id") is not None]
        async with Database().session() as session:
            settings = (await session.execute(select(StackVaultPricingSettings))).scalars().first()
            markup = getattr(settings, "default_markup_percentage", None)
            overrides = (await session.execute(
                select(StackVaultPriceOverride).where(StackVaultPriceOverride.product_id.in_(ids))
            )).scalars().all() if ids else []
        override_map = {str(row.product_id): row.manual_price for row in overrides}
        for product in catalog_products:
            override = override_map.get(str(product.get("id")))
            if override is not None:
                product["manual_price"] = override
        products = await prepare_catalog(catalog_products)
        for product in products:
            product["manual_price"] = override_map.get(str(product.get("id")))
    except StackVaultAPIError as exc:
        error = f"تعذر جلب منتجات StackVault: {exc}"
    except Exception:
        logger.exception("StackVault pricing page failed")
        error = "تعذر الوصول إلى قاعدة البيانات أو تجهيز بيانات التسعير."

    return templates.TemplateResponse(
        request,
        "stackvault_pricing.html",
        {"title": "تسعير StackVault", "products": products, "markup": markup, "error": error, "success": success},
    )


class StackVaultPricingView(BaseView):
    name = "تسعير StackVault"
    icon = "fa-solid fa-tags"

    @expose("/stackvault-pricing", methods=["GET", "POST"])
    async def pricing(self, request: Request):
        return await stackvault_pricing_page(request)


# App Factory
def create_admin_app(bot: Any = None) -> Starlette:
    """Build the admin panel app."""
    set_notifier_bot(bot)

    from bot.web.export import export_routes

    async def root_redirect(request: Request) -> RedirectResponse:
        return RedirectResponse(url="/admin")

    routes = [
        Route("/", root_redirect),
        Route("/health", health_check),
        Route("/metrics", metrics_json),
        Route("/metrics/prometheus", prometheus_metrics),
    ] + export_routes

    app = Starlette(routes=routes)
    app.add_middleware(
        SessionMiddleware,
        secret_key=EnvKeys.SECRET_KEY,
        max_age=1800,
        https_only=EnvKeys.session_cookie_secure(),
        same_site="strict",
    )

    auth_backend = AdminAuth(secret_key=EnvKeys.SECRET_KEY)
    admin = Admin(
        app,
        engine=Database().engine,
        authentication_backend=auth_backend,
        title="لوحة إدارة منصة اليمن الإلكترونية",
        # Override the (blank) SQLAdmin index page with our help/cheat-sheet.
        templates_dir=os.path.join(os.path.dirname(__file__), "templates"),
    )

    admin.add_view(UserAdmin)
    admin.add_view(RoleAdmin)
    admin.add_view(CategoryAdmin)
    admin.add_view(GoodsAdmin)
    admin.add_view(ItemValuesAdmin)
    admin.add_view(BoughtGoodsAdmin)
    admin.add_view(OperationsAdmin)
    admin.add_view(PaymentsAdmin)
    admin.add_view(ReferralEarningsAdmin)
    admin.add_view(AuditLogAdmin)
    admin.add_view(PromoCodeAdmin)
    admin.add_view(CartItemsAdmin)
    admin.add_view(StackVaultPricingView)
    if EnvKeys.REVIEWS_ENABLED == "1":
        admin.add_view(ReviewsAdmin)

    return app
