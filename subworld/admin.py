"""SubWorld admin panel (SQLAdmin, same style as the Yemen bot's panel).

Arabic labels, RTL layout, dark/light theme toggle (see templates/).
Mounted at /subworld by the Yemen bot's admin server (see
Apply-SubWorld-Admin-Patch.ps1). Same login as the Yemen panel
(AdminAuth + SECRET_KEY); separate database, so no view here can
touch Yemen data.
"""
from __future__ import annotations

import os

from sqladmin import Admin, ModelView
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

from .db import get_engine
from .models import Category, Order, Product, ProductCode, SubworldUser
from .supplier_models import (
    Supplier,
    SupplierCategoryMarkup,
    SupplierPriceOverride,
    SupplierPricingRule,
    SupplierProduct,
    SupplierSyncLog,
)


class SupplierAdmin(ModelView, model=Supplier):
    """External suppliers (StackVault / Pandora / Vente)."""

    name = "إدارة الموردين"
    name_plural = "إدارة الموردين"
    icon = "fa-solid fa-truck"

    column_list = [
        Supplier.id,
        Supplier.icon_emoji,
        Supplier.display_name,
        Supplier.name,
        Supplier.api_url,
        Supplier.profit_margin,
        Supplier.sync_enabled,
        Supplier.sync_interval_minutes,
        Supplier.last_sync_at,
        Supplier.last_sync_status,
        Supplier.last_sync_product_count,
        Supplier.sync_errors_count,
        Supplier.is_active,
    ]
    column_searchable_list = [Supplier.name, Supplier.display_name]
    column_sortable_list = [
        Supplier.id,
        Supplier.sort_order,
        Supplier.is_active,
        Supplier.last_sync_at,
    ]
    column_default_sort = [("sort_order", False), ("id", False)]
    column_filters = [Supplier.is_active, Supplier.last_sync_status]
    column_labels = {
        "icon_emoji": "الأيقونة",
        "display_name": "الاسم المعروض",
        "name": "الاسم الداخلي",
        "api_url": "رابط API",
        "api_key": "مفتاح API",
        "profit_margin": "هامش الربح %",
        "style": "اللون",
        "is_active": "مفعّل",
        "sort_order": "الترتيب",
        "sync_enabled": "المزامنة التلقائية",
        "sync_interval_minutes": "فاصل المزامنة (دقيقة)",
        "last_sync_at": "آخر مزامنة",
        "last_sync_status": "حالة المزامنة",
        "last_sync_error": "خطأ المزامنة",
        "last_sync_product_count": "عدد المنتجات",
        "sync_errors_count": "عدد الأخطاء",
        "notes": "ملاحظات",
    }
    # api_key is editable in the form (panel-driven connection) but never
    # shown in the list view.
    form_columns = [
        Supplier.name,
        Supplier.display_name,
        Supplier.icon_emoji,
        Supplier.api_url,
        Supplier.api_key,
        Supplier.profit_margin,
        Supplier.style,
        Supplier.sort_order,
        Supplier.sync_enabled,
        Supplier.sync_interval_minutes,
        Supplier.is_active,
        Supplier.notes,
    ]
    page_size = 25


class SupplierProductAdmin(ModelView, model=SupplierProduct):
    """Products fetched from external suppliers."""

    name = "منتجات الموردين"
    name_plural = "منتجات الموردين"
    icon = "fa-solid fa-boxes-stacked"

    column_list = [
        SupplierProduct.id,
        SupplierProduct.supplier_id,
        SupplierProduct.name,
        SupplierProduct.category,
        SupplierProduct.cost_price,
        SupplierProduct.fixed_profit,
        SupplierProduct.selling_price,
        SupplierProduct.manual_price,
        SupplierProduct.stock,
        SupplierProduct.is_visible,
        SupplierProduct.show_in_local,
        SupplierProduct.is_available,
        SupplierProduct.last_synced_at,
    ]
    column_searchable_list = [
        SupplierProduct.name,
        SupplierProduct.external_id,
    ]
    column_sortable_list = [
        SupplierProduct.id,
        SupplierProduct.cost_price,
        SupplierProduct.selling_price,
        SupplierProduct.stock,
        SupplierProduct.last_synced_at,
    ]
    column_default_sort = [("id", True)]
    column_filters = [
        SupplierProduct.supplier_id,
        SupplierProduct.category,
        SupplierProduct.is_visible,
        SupplierProduct.is_available,
        SupplierProduct.show_in_local,
    ]
    column_labels = {
        "supplier_id": "المورد",
        "external_id": "المعرّف الخارجي",
        "name": "الاسم",
        "category": "الفئة",
        "cost_price": "سعر التكلفة",
        "selling_price": "سعر البيع",
        "manual_price": "سعر يدوي (نهائي)",
        "fixed_profit": "ربح ثابت",
        "stock": "المخزون",
        "is_visible": "ظاهر",
        "show_in_local": "في المميزة",
        "is_available": "متوفر",
        "last_synced_at": "آخر مزامنة",
    }
    page_size = 50


class SupplierPricingRuleAdmin(ModelView, model=SupplierPricingRule):
    """Pricing rules per supplier (markup percent/fixed)."""

    name = "قواعد التسعير"
    name_plural = "قواعد تسعير الموردين"
    icon = "fa-solid fa-tags"

    column_list = [
        SupplierPricingRule.id,
        SupplierPricingRule.supplier_id,
        SupplierPricingRule.markup_percent,
        SupplierPricingRule.markup_fixed,
        SupplierPricingRule.default_fixed_profit,
        SupplierPricingRule.min_margin_percent,
        SupplierPricingRule.min_selling_price,
        SupplierPricingRule.max_selling_price,
        SupplierPricingRule.round_to,
        SupplierPricingRule.updated_at,
    ]
    column_sortable_list = [
        SupplierPricingRule.id,
        SupplierPricingRule.supplier_id,
        SupplierPricingRule.markup_percent,
    ]
    column_default_sort = [("id", True)]
    column_labels = {
        "supplier_id": "المورد",
        "markup_percent": "نسبة الربح %",
        "markup_fixed": "زيادة ثابتة",
        "default_fixed_profit": "ربح ثابت افتراضي",
        "min_margin_percent": "حد أدنى للربح %",
        "min_selling_price": "أدنى سعر بيع",
        "max_selling_price": "أعلى سعر بيع",
        "round_to": "تقريب إلى",
        "updated_at": "آخر تحديث",
    }
    page_size = 25


class SupplierCategoryMarkupAdmin(ModelView, model=SupplierCategoryMarkup):
    """Category-specific markup override for a supplier."""

    name = "هوامش الفئات"
    name_plural = "هوامش فئات الموردين"
    icon = "fa-solid fa-layer-group"

    column_list = [
        SupplierCategoryMarkup.id,
        SupplierCategoryMarkup.supplier_id,
        SupplierCategoryMarkup.category_keyword,
        SupplierCategoryMarkup.markup_percent,
        SupplierCategoryMarkup.fixed_profit,
    ]
    column_searchable_list = [SupplierCategoryMarkup.category_keyword]
    column_default_sort = [("id", True)]
    column_labels = {
        "supplier_id": "المورد",
        "category_keyword": "كلمة الفئة",
        "markup_percent": "نسبة الربح %",
        "fixed_profit": "ربح ثابت",
    }
    page_size = 25


class SupplierPriceOverrideAdmin(ModelView, model=SupplierPriceOverride):
    """A fixed price override for a specific product."""

    name = "تجاوزات الأسعار"
    name_plural = "تجاوزات أسعار المنتجات"
    icon = "fa-solid fa-tag"

    column_list = [
        SupplierPriceOverride.id,
        SupplierPriceOverride.supplier_id,
        SupplierPriceOverride.external_id,
        SupplierPriceOverride.fixed_price,
        SupplierPriceOverride.reason,
        SupplierPriceOverride.created_at,
    ]
    column_searchable_list = [SupplierPriceOverride.external_id]
    column_default_sort = [("id", True)]
    column_labels = {
        "supplier_id": "المورد",
        "external_id": "المعرّف الخارجي",
        "fixed_price": "السعر الثابت",
        "reason": "السبب",
        "created_at": "أُنشئ",
    }
    page_size = 25


class SupplierSyncLogAdmin(ModelView, model=SupplierSyncLog):
    """Log entries for sync operations (read-only)."""

    name = "سجلات المزامنة"
    name_plural = "سجلات مزامنة الموردين"
    icon = "fa-solid fa-clock-rotate-left"

    can_create = False
    can_edit = False
    can_delete = False

    column_list = [
        SupplierSyncLog.id,
        SupplierSyncLog.supplier_id,
        SupplierSyncLog.started_at,
        SupplierSyncLog.finished_at,
        SupplierSyncLog.status,
        SupplierSyncLog.products_total,
        SupplierSyncLog.products_added,
        SupplierSyncLog.products_updated,
        SupplierSyncLog.products_removed,
        SupplierSyncLog.error_message,
    ]
    column_sortable_list = [SupplierSyncLog.id, SupplierSyncLog.started_at]
    column_default_sort = [("id", True)]
    column_filters = [SupplierSyncLog.supplier_id, SupplierSyncLog.status]
    column_labels = {
        "supplier_id": "المورد",
        "started_at": "بدأ",
        "finished_at": "انتهى",
        "status": "الحالة",
        "products_total": "الإجمالي",
        "products_added": "أُضيف",
        "products_updated": "حُدث",
        "products_removed": "أُزيل",
        "price_changes": "تغيرات الأسعار",
        "error_message": "الخطأ",
        "details": "التفاصيل",
    }
    page_size = 25


class FeaturedProductAdmin(ModelView, model=Product):
    """Featured subscriptions (the Yemen bot's local subscriptions)."""

    name = "اشتراك مميز"
    name_plural = "الاشتراكات المميزة"
    icon = "fa-solid fa-star"

    column_list = [
        Product.id,
        Product.name_ar,
        Product.price_usd,
        Product.category_id,
        Product.delivery_type,
        Product.quantity,
        Product.codes_per_purchase,
        Product.is_active,
        Product.sort_order,
    ]
    column_searchable_list = [Product.name_ar, Product.name_en]
    column_sortable_list = [Product.id, Product.price_usd, Product.sort_order]
    column_default_sort = [("sort_order", False), ("id", False)]
    column_filters = [Product.is_active, Product.delivery_type, Product.category_id]
    column_labels = {
        "category_id": "الفئة",
        "name_ar": "الاسم (عربي)",
        "name_en": "الاسم (إنجليزي)",
        "description_ar": "الوصف (عربي)",
        "description_en": "الوصف (إنجليزي)",
        "price_usd": "السعر (دولار)",
        "price_sar": "السعر (ريال سعودي)",
        "duration_days": "المدة (يوم)",
        "delivery_type": "طريقة التسليم",
        "codes_per_purchase": "أكواد لكل عملية شراء",
        "invite_field_type": "بيانات الدعوة",
        "quantity": "الكمية",
        "sort_order": "الترتيب",
        "is_active": "مفعّل",
    }
    form_columns = [
        Product.category_id,
        Product.name_ar,
        Product.name_en,
        Product.description_ar,
        Product.description_en,
        Product.price_usd,
        Product.duration_days,
        Product.delivery_type,
        Product.codes_per_purchase,
        Product.invite_field_type,
        Product.quantity,
        Product.sort_order,
        Product.is_active,
    ]
    page_size = 25


class ProductCodeAdmin(ModelView, model=ProductCode):
    """One redeemable code/key per featured subscription unit."""

    name = "كود اشتراك"
    name_plural = "أكواد الاشتراكات"
    icon = "fa-solid fa-key"

    column_list = [
        ProductCode.id,
        ProductCode.product_id,
        ProductCode.code,
        ProductCode.is_used,
        ProductCode.used_by_user_id,
        ProductCode.used_at,
    ]
    column_searchable_list = [ProductCode.code]
    column_sortable_list = [ProductCode.id, ProductCode.used_at]
    column_default_sort = [("id", True)]
    column_filters = [ProductCode.is_used, ProductCode.product_id]
    column_labels = {
        "product_id": "الاشتراك",
        "code": "الكود",
        "is_used": "مستخدم",
        "used_by_user_id": "استخدمه",
        "used_at": "وقت الاستخدام",
    }
    page_size = 50


class CategoryAdmin(ModelView, model=Category):
    """Product categories."""

    name = "فئة"
    name_plural = "الفئات"
    icon = "fa-solid fa-folder"

    column_list = [
        Category.id,
        Category.name_ar,
        Category.name_en,
        Category.sort_order,
        Category.is_active,
    ]
    column_default_sort = [("sort_order", False), ("id", False)]
    column_labels = {
        "name_ar": "الاسم (عربي)",
        "name_en": "الاسم (إنجليزي)",
        "sort_order": "الترتيب",
        "is_active": "مفعّلة",
    }
    page_size = 25


class SubworldUserAdmin(ModelView, model=SubworldUser):
    """SubWorld users and wallet balances."""

    name = "مستخدم"
    name_plural = "المستخدمون"
    icon = "fa-solid fa-users"

    column_list = [
        SubworldUser.id,
        SubworldUser.language,
        SubworldUser.balance_usd,
        SubworldUser.created_at,
    ]
    column_searchable_list = [SubworldUser.id]
    column_sortable_list = [SubworldUser.id, SubworldUser.balance_usd, SubworldUser.created_at]
    column_default_sort = [("id", True)]
    column_labels = {
        "id": "معرّف تليجرام",
        "language": "اللغة",
        "balance_usd": "الرصيد (دولار)",
        "created_at": "أُنشئ",
    }
    # Balance is editable here (manual top-up until the wallet proof flow lands).
    form_columns = [SubworldUser.language, SubworldUser.balance_usd]
    page_size = 25


class OrderAdmin(ModelView, model=Order):
    """Purchase orders (created by the bot; status edited by the admin)."""

    name = "طلب"
    name_plural = "الطلبات"
    icon = "fa-solid fa-receipt"

    can_create = False
    can_delete = False

    column_list = [
        Order.id,
        Order.user_id,
        Order.product_id,
        Order.supplier_product_id,
        Order.amount_usd,
        Order.status,
        Order.code_id,
        Order.created_at,
    ]
    column_sortable_list = [Order.id, Order.created_at, Order.amount_usd]
    column_default_sort = [("id", True)]
    column_filters = [Order.status]
    column_labels = {
        "user_id": "المشتري",
        "product_id": "الاشتراك المميز",
        "supplier_product_id": "منتج المورد",
        "amount_usd": "المبلغ (دولار)",
        "status": "الحالة",
        "proof": "الإثبات",
        "code_id": "الكود",
        "created_at": "أُنشئ",
    }
    # Only the status is editable (pending -> delivered for manual delivery).
    form_columns = [Order.status]
    page_size = 25


def create_subworld_admin_app() -> Starlette:
    """Build the SubWorld admin panel as its own Starlette app.

    Served at /subworld by the Yemen bot's admin server through a small
    ASGI dispatcher (see the mount block in bot/web/admin.py). Same login
    as the Yemen panel (AdminAuth + SECRET_KEY); separate database.
    The sub-app carries its own SessionMiddleware with the same settings
    as the Yemen panel, because the dispatcher bypasses the parent app's
    middleware stack.
    """
    from bot.web.admin import AdminAuth
    from bot.misc import EnvKeys
    from starlette.middleware.sessions import SessionMiddleware

    async def root_redirect(request):
        return RedirectResponse(url="/subworld/admin/")

    app = Starlette(routes=[Route("/", root_redirect)])
    app.add_middleware(
        SessionMiddleware,
        secret_key=EnvKeys.SECRET_KEY,
        max_age=1800,
        https_only=EnvKeys.session_cookie_secure(),
        same_site="strict",
    )
    admin = Admin(
        app,
        engine=get_engine(),
        authentication_backend=AdminAuth(secret_key=EnvKeys.SECRET_KEY),
        title="لوحة إدارة SubWorld",
        templates_dir=os.path.join(os.path.dirname(__file__), "templates"),
    )
    for view in (
        SupplierAdmin,
        SupplierProductAdmin,
        SupplierPricingRuleAdmin,
        SupplierCategoryMarkupAdmin,
        SupplierPriceOverrideAdmin,
        SupplierSyncLogAdmin,
        FeaturedProductAdmin,
        ProductCodeAdmin,
        CategoryAdmin,
        SubworldUserAdmin,
        OrderAdmin,
    ):
        admin.add_view(view)
    return app
