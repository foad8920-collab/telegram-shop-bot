"""SubWorld store catalog: browse local products and supplier products.

Prices are USD only. Buying pays from the wallet balance:
- local "codes" products deliver a code immediately;
- supplier / invite / manual products create a pending order for the admin.
Wallet top-up (Binance/crypto proof + admin approval) is the next step;
until then a purchase with insufficient balance explains how to top up.

Store lists are paginated (10 products per page) with next/previous
buttons. Supplier stores first show their categories; a product moved
to Featured in the admin panel (show_in_local) appears in the Featured
list instead of its supplier store. Stock is marked 🟢 in stock /
🔴 out of stock on every product button (Telegram buttons have no
colour - the dot is the marker).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import F, Router, types
from sqlalchemy import func, select

from .db import get_session_factory
from .keyboards import CHOOSE_TEXT, TEXTS, choose_kb, get_lang, main_menu_kb
from .models import Order, Product, ProductCode, SubworldUser
from .supplier_models import Supplier, SupplierProduct

logger = logging.getLogger("subworld.catalog")
catalog_router = Router()

PAGE_SIZE = 10

# Last store list each user opened, so a product detail's Back button
# returns to the exact page/category (in-memory; falls back to the
# section screen when unknown, e.g. after a restart).
_LAST_LIST: dict[int, str] = {}


def _db_on() -> bool:
    return bool(os.environ.get("SUBWORLD_DATABASE_URL", "").strip())


def _session():
    return get_session_factory()()


def _t(lang: str, key: str) -> str:
    strings = {
        "store_unavailable": {
            "ar": "المتجر غير متاح حاليا (قاعدة البيانات غير مضبوطة).",
            "en": "The store is unavailable right now (database is not configured).",
        },
        "store_title": {
            "ar": "🛍️ المتجر\nاختر القسم:",
            "en": "🛍️ Store\nChoose a section:",
        },
        "local_section": {"ar": "⭐ الاشتراكات المميزة", "en": "⭐ Featured subscriptions"},
        "local_title": {"ar": "⭐ الاشتراكات المميزة:", "en": "⭐ Featured subscriptions:"},
        "empty": {"ar": "لا منتجات حاليا.", "en": "No products right now."},
        "back": {"ar": "🔙 رجوع", "en": "🔙 Back"},
        "main_menu": {"ar": "🔙 القائمة الرئيسية", "en": "🔙 Main menu"},
        "price": {"ar": "💲 السعر", "en": "💲 Price"},
        "stock": {"ar": "📦 المخزون", "en": "📦 Stock"},
        "unlimited": {"ar": "غير محدود", "en": "Unlimited"},
        "duration": {"ar": "⏳ المدة", "en": "⏳ Duration"},
        "days": {"ar": "يوم", "en": "days"},
        "codes_left": {"ar": "كود متاح", "en": "codes available"},
        "buy": {"ar": "🛒 شراء", "en": "🛒 Buy"},
        "balance_title": {"ar": "💰 رصيدك", "en": "💰 Your balance"},
        "balance_line": {"ar": "رصيدك الحالي", "en": "Your current balance"},
        "topup_hint": {
            "ar": "اشحن محفظتك عبر Binance أو الكريبتو، ثم أرسل إثبات الدفع ليضيف المشرف الرصيد.",
            "en": "Top up your wallet via Binance or crypto, then send the payment proof so the admin adds the balance.",
        },
        "not_enough": {"ar": "رصيدك غير كافٍ لهذا المنتج.", "en": "Your balance is not enough for this product."},
        "bought": {"ar": "✅ تم الشراء بنجاح!", "en": "✅ Purchase successful!"},
        "your_code": {"ar": "الكود الخاص بك:", "en": "Your code:"},
        "remaining": {"ar": "رصيدك المتبقي", "en": "Your remaining balance"},
        "order_created": {
            "ar": "✅ تم إنشاء طلبك!\nسيصلك المنتج بعد تأكيد المشرف.",
            "en": "✅ Your order was created!\nYou will receive the product after admin confirmation.",
        },
        "no_codes": {"ar": "⛔ نفدت أكواد هذا المنتج حاليا.", "en": "⛔ This product's codes are sold out right now."},
        "gone": {"ar": "المنتج غير متاح حاليا.", "en": "This product is unavailable right now."},
        "prev": {"ar": "⬅️ السابق", "en": "⬅️ Previous"},
        "next": {"ar": "التالي ➡️", "en": "Next ➡️"},
        "page": {"ar": "صفحة", "en": "Page"},
        "of": {"ar": "من", "en": "of"},
        "categories": {"ar": "📁 الفئات:", "en": "📁 Categories:"},
        "all_products": {"ar": "📦 عرض الكل", "en": "📦 Show all"},
        "other_category": {"ar": "أخرى", "en": "Other"},
        "out_of_stock": {"ar": "⛔ نفدت الكمية", "en": "⛔ Out of stock"},
    }
    return strings[key][lang]


def _fmt_price(value) -> str:
    return f"${Decimal(str(value)):.2f}"


def _paginate(items: list, page: int, per_page: int = PAGE_SIZE):
    """Return (page_items, total_pages, clamped_page)."""
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], pages, page


def _supplier_buyable(product: SupplierProduct) -> bool:
    """True when the product can actually be bought right now."""
    if not product.is_available:
        return False
    if product.is_unlimited:
        return True
    return (product.stock or 0) > 0


def _supplier_dot(product: SupplierProduct) -> str:
    return "🟢" if _supplier_buyable(product) else "🔴"


def _remember_list(user_id: int, callback_data: str) -> None:
    _LAST_LIST[user_id] = callback_data


def _back_to_list(user_id: int, fallback: str) -> str:
    target = _LAST_LIST.get(user_id) or fallback
    return target if target.startswith("shop:") else fallback


async def _get_user(session, tg_id: int, lang: str) -> SubworldUser:
    user = await session.get(SubworldUser, tg_id)
    if user is None:
        user = SubworldUser(id=tg_id, language=lang, balance_usd=Decimal("0"))
        session.add(user)
        await session.flush()
    return user


def _back_kb(lang: str, target: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=_t(lang, "back"), callback_data=target)],
            [types.InlineKeyboardButton(text=_t(lang, "main_menu"), callback_data="menu:main")],
        ]
    )


def _nav_row(lang: str, page: int, pages: int, page_cb) -> list:
    """Previous / Next buttons for the current page (one row, may be empty)."""
    row = []
    if page > 1:
        row.append(types.InlineKeyboardButton(text=_t(lang, "prev"), callback_data=page_cb(page - 1)))
    if page < pages:
        row.append(types.InlineKeyboardButton(text=_t(lang, "next"), callback_data=page_cb(page + 1)))
    return [row] if row else []


# ============================================================
#  Main menu / balance / language
# ============================================================

@catalog_router.callback_query(F.data == "menu:main")
async def on_menu_main(callback: types.CallbackQuery) -> None:
    # The main menu is a reply keyboard (buttons.py), which cannot be
    # attached to an edited message: send it as a new message and clear
    # the old message's inline keyboard.
    lang = get_lang(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    from .buttons import send_main_menu

    await send_main_menu(callback.message, lang)
    await callback.answer()


@catalog_router.callback_query(F.data == "menu:lang")
async def on_menu_lang(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(CHOOSE_TEXT, reply_markup=choose_kb())
    await callback.answer()


def _balance_text(balance: Decimal, lang: str) -> str:
    return (
        f"{_t(lang, 'balance_title')}\n"
        f"{_t(lang, 'balance_line')}: {_fmt_price(balance)}\n\n"
        f"{_t(lang, 'topup_hint')}"
    )


async def _load_balance(user_id: int, lang: str) -> Decimal | None:
    try:
        async with _session() as session:
            user = await _get_user(session, user_id, lang)
            return Decimal(str(user.balance_usd or 0))
    except Exception:
        logger.exception("SubWorld balance read failed")
        return None


async def send_balance(message: types.Message, lang: str) -> None:
    """Balance as a new message (used by the reply-keyboard button)."""
    if not _db_on():
        await message.answer(_t(lang, "store_unavailable"))
        return
    balance = await _load_balance(message.from_user.id, lang)
    if balance is None:
        await message.answer(_t(lang, "store_unavailable"))
        return
    await message.answer(_balance_text(balance, lang))


@catalog_router.callback_query(F.data == "menu:balance")
async def on_menu_balance(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    balance = await _load_balance(callback.from_user.id, lang)
    if balance is None:
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    await callback.message.edit_text(
        _balance_text(balance, lang), reply_markup=_back_kb(lang, "menu:main")
    )
    await callback.answer()


# ============================================================
#  Store sections
# ============================================================

async def _load_store_suppliers(session):
    rows = (
        await session.execute(
            select(Supplier)
            .where(Supplier.is_active == True)  # noqa: E712
            .order_by(Supplier.sort_order, Supplier.id)
        )
    ).scalars().all()
    return [r for r in rows if not (r.api_url or "").strip().startswith("local://")]


def _store_sections_kb(lang: str, suppliers) -> types.InlineKeyboardMarkup:
    buttons = [[types.InlineKeyboardButton(text=_t(lang, "local_section"), callback_data="shop:local_list")]]
    for sup in suppliers:
        label = (sup.display_name or sup.name or f"Supplier {sup.id}")[:40]
        buttons.append([types.InlineKeyboardButton(text=f"🏬 {label}", callback_data=f"shop:sup:{sup.id}")])
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "main_menu"), callback_data="menu:main")])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_store_sections(message: types.Message, lang: str) -> None:
    """Store sections as a new message (used by the reply-keyboard button)."""
    if not _db_on():
        await message.answer(_t(lang, "store_unavailable"))
        return
    try:
        async with _session() as session:
            suppliers = await _load_store_suppliers(session)
    except Exception:
        logger.exception("SubWorld store sections failed")
        await message.answer(_t(lang, "store_unavailable"))
        return
    await message.answer(_t(lang, "store_title"), reply_markup=_store_sections_kb(lang, suppliers))


@catalog_router.callback_query(F.data == "menu:shop")
async def on_menu_shop(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    try:
        async with _session() as session:
            suppliers = await _load_store_suppliers(session)
    except Exception:
        logger.exception("SubWorld store sections failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    await callback.message.edit_text(
        _t(lang, "store_title"),
        reply_markup=_store_sections_kb(lang, suppliers),
    )
    await callback.answer()


# ============================================================
#  Featured subscriptions (local products + products moved here
#  from a supplier via show_in_local in the admin panel)
# ============================================================

async def _render_local_list(callback: types.CallbackQuery, page: int) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        async with _session() as session:
            products = (await session.execute(
                select(Product).where(Product.is_active == True).order_by(Product.sort_order, Product.id)  # noqa: E712
            )).scalars().all()
            codes_rows = (await session.execute(
                select(ProductCode.product_id, func.count()).where(
                    ProductCode.is_used == False  # noqa: E712
                ).group_by(ProductCode.product_id)
            )).all()
            codes_left = {pid: n for pid, n in codes_rows}
            moved = (await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.is_visible == True,  # noqa: E712
                    SupplierProduct.show_in_local == True,  # noqa: E712
                ).order_by(SupplierProduct.selling_price, SupplierProduct.id)
            )).scalars().all()
    except Exception:
        logger.exception("SubWorld local list failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    items = []  # (dot, label, callback_data)
    for p in products:
        name = (p.name_en if lang == "en" else p.name_ar)[:28]
        if (p.delivery_type or "codes") == "codes":
            dot = "🟢" if codes_left.get(p.id, 0) > 0 else "🔴"
        else:
            dot = "🟢" if (p.quantity is None or p.quantity > 0) else "🔴"
        items.append((dot, f"{name} — {_fmt_price(p.price_usd)}", f"shop:local:{p.id}"))
    for p in moved:
        items.append((_supplier_dot(p), f"{p.name[:28]} — {_fmt_price(p.selling_price or 0)}", f"shop:sprod:{p.id}"))
    if not items:
        await callback.message.edit_text(_t(lang, "empty"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    page_items, pages, page = _paginate(items, page)
    list_cb = f"shop:feat:{page}" if page > 1 else "shop:local_list"
    _remember_list(callback.from_user.id, list_cb)
    buttons = [
        [types.InlineKeyboardButton(text=f"{dot} {label}", callback_data=cb)]
        for dot, label, cb in page_items
    ]
    buttons += _nav_row(lang, page, pages, lambda p: f"shop:feat:{p}")
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "back"), callback_data="menu:shop")])
    title = _t(lang, "local_title")
    if pages > 1:
        title += f"\n{_t(lang, 'page')} {page} {_t(lang, 'of')} {pages}"
    await callback.message.edit_text(title, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@catalog_router.callback_query(F.data == "shop:local_list")
async def on_local_list(callback: types.CallbackQuery) -> None:
    await _render_local_list(callback, 1)


@catalog_router.callback_query(F.data.startswith("shop:feat:"))
async def on_local_list_page(callback: types.CallbackQuery) -> None:
    try:
        page = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        page = 1
    await _render_local_list(callback, page)


# ============================================================
#  Supplier stores: categories first, then paginated products
# ============================================================

async def _supplier_categories(session, supplier_id: int):
    """Sorted distinct categories of the supplier's visible products.

    Returns a list of category names; None stands for products without
    a category (shown as "Other"). Moved-to-Featured products are excluded.
    """
    rows = (await session.execute(
        select(SupplierProduct.category).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.is_visible == True,  # noqa: E712
            SupplierProduct.show_in_local == False,  # noqa: E712
        ).distinct()
    )).scalars().all()
    named = sorted({(c or "").strip() for c in rows if (c or "").strip()})
    has_other = any(not (c or "").strip() for c in rows)
    return named + ([None] if has_other else [])


@catalog_router.callback_query(F.data.startswith("shop:sup:"))
async def on_supplier_categories(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        supplier_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    try:
        async with _session() as session:
            supplier = await session.get(Supplier, supplier_id)
            cats = await _supplier_categories(session, supplier_id)
            total = (await session.execute(
                select(func.count()).select_from(SupplierProduct).where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.is_visible == True,  # noqa: E712
                    SupplierProduct.show_in_local == False,  # noqa: E712
                )
            )).scalar_one()
    except Exception:
        logger.exception("SubWorld supplier categories failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    title = (supplier.display_name or supplier.name) if supplier else f"Supplier {supplier_id}"
    if total == 0:
        await callback.message.edit_text(f"🏬 {title}\n{_t(lang, 'empty')}", reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    if len(cats) <= 1:
        # One category (or none): skip the category screen entirely.
        await _render_supplier_products(callback, supplier_id, None, 1)
        return
    buttons = []
    for idx, cat in enumerate(cats):
        label = cat if cat is not None else _t(lang, "other_category")
        buttons.append([types.InlineKeyboardButton(
            text=f"📁 {label}",
            callback_data=f"shop:supc:{supplier_id}:{idx}:1",
        )])
    buttons.append([types.InlineKeyboardButton(
        text=_t(lang, "all_products"),
        callback_data=f"shop:supa:{supplier_id}:1",
    )])
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "back"), callback_data="menu:shop")])
    await callback.message.edit_text(
        f"🏬 {title}\n{_t(lang, 'categories')}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


async def _render_supplier_products(
    callback: types.CallbackQuery, supplier_id: int, cat_idx: int | None, page: int
) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        async with _session() as session:
            supplier = await session.get(Supplier, supplier_id)
            cats = await _supplier_categories(session, supplier_id)
            cat = None
            if cat_idx is not None:
                if cat_idx < 0 or cat_idx >= len(cats):
                    # Stale category index (categories changed since the
                    # button was drawn): fall back to the category screen.
                    await on_supplier_categories(callback)
                    return
                cat = cats[cat_idx]
            stmt = select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.is_visible == True,  # noqa: E712
                SupplierProduct.show_in_local == False,  # noqa: E712
            )
            if cat is None and cat_idx is not None:
                stmt = stmt.where(
                    (SupplierProduct.category.is_(None)) | (SupplierProduct.category == "")
                )
            elif cat is not None:
                stmt = stmt.where(SupplierProduct.category == cat)
            rows = (await session.execute(
                stmt.order_by(
                    SupplierProduct.is_available.desc(),
                    SupplierProduct.selling_price,
                    SupplierProduct.id,
                )
            )).scalars().all()
    except Exception:
        logger.exception("SubWorld supplier list failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    title = (supplier.display_name or supplier.name) if supplier else f"Supplier {supplier_id}"
    if cat is not None:
        title += f" — {cat}"
    elif cat_idx is not None:
        title += f" — {_t(lang, 'other_category')}"
    if not rows:
        await callback.message.edit_text(f"🏬 {title}\n{_t(lang, 'empty')}", reply_markup=_back_kb(lang, f"shop:sup:{supplier_id}"))
        await callback.answer()
        return
    page_rows, pages, page = _paginate(rows, page)
    if cat_idx is None:
        list_cb = f"shop:supa:{supplier_id}:{page}"
        page_cb = lambda p: f"shop:supa:{supplier_id}:{p}"  # noqa: E731
    else:
        list_cb = f"shop:supc:{supplier_id}:{cat_idx}:{page}"
        page_cb = lambda p: f"shop:supc:{supplier_id}:{cat_idx}:{p}"  # noqa: E731
    _remember_list(callback.from_user.id, list_cb)
    buttons = [
        [types.InlineKeyboardButton(
            text=f"{_supplier_dot(p)} {p.name[:28]} — {_fmt_price(p.selling_price or 0)}",
            callback_data=f"shop:sprod:{p.id}",
        )]
        for p in page_rows
    ]
    buttons += _nav_row(lang, page, pages, page_cb)
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "back"), callback_data=f"shop:sup:{supplier_id}")])
    text = f"🏬 {title}:"
    if pages > 1:
        text += f"\n{_t(lang, 'page')} {page} {_t(lang, 'of')} {pages}"
    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@catalog_router.callback_query(F.data.startswith("shop:supc:"))
async def on_supplier_category_page(callback: types.CallbackQuery) -> None:
    try:
        _, _, raw_sup, raw_cat, raw_page = (callback.data or "").split(":")
        supplier_id, cat_idx, page = int(raw_sup), int(raw_cat), int(raw_page)
    except (IndexError, ValueError):
        await callback.answer()
        return
    await _render_supplier_products(callback, supplier_id, cat_idx, page)


@catalog_router.callback_query(F.data.startswith("shop:supa:"))
async def on_supplier_all_page(callback: types.CallbackQuery) -> None:
    try:
        _, _, raw_sup, raw_page = (callback.data or "").split(":")
        supplier_id, page = int(raw_sup), int(raw_page)
    except (IndexError, ValueError):
        await callback.answer()
        return
    await _render_supplier_products(callback, supplier_id, None, page)


# ============================================================
#  Product details
# ============================================================

@catalog_router.callback_query(F.data.startswith("shop:local:"))
async def on_local_detail(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        product_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    try:
        async with _session() as session:
            product = await session.get(Product, product_id)
            codes_left = None
            if product is not None and (product.delivery_type or "codes") == "codes":
                codes_left = (await session.execute(
                    select(ProductCode.id).where(
                        ProductCode.product_id == product_id,
                        ProductCode.is_used == False,  # noqa: E712
                    )
                )).scalars().all()
                codes_left = len(codes_left)
    except Exception:
        logger.exception("SubWorld local detail failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "shop:local_list"))
        await callback.answer()
        return
    if product is None or not product.is_active:
        await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, "shop:local_list"))
        await callback.answer()
        return
    name = product.name_en if lang == "en" else product.name_ar
    desc = (product.description_en if lang == "en" else product.description_ar) or ""
    lines = [f"<b>{name}</b>"]
    if desc:
        lines.append(desc[:800])
    lines.append(f"{_t(lang, 'price')}: {_fmt_price(product.price_usd)}")
    if product.duration_days:
        lines.append(f"{_t(lang, 'duration')}: {product.duration_days} {_t(lang, 'days')}")
    if codes_left is not None:
        lines.append(f"{_t(lang, 'stock')}: {codes_left} {_t(lang, 'codes_left')}")
    elif (product.delivery_type or "codes") != "codes" and product.quantity is not None:
        lines.append(f"{_t(lang, 'stock')}: {product.quantity}")
    if (product.delivery_type or "codes") == "codes":
        buyable = (codes_left or 0) > 0
    else:
        buyable = product.quantity is None or product.quantity > 0
    rows = []
    if buyable:
        rows.append([types.InlineKeyboardButton(text=_t(lang, "buy"), callback_data=f"shop:buy:local:{product.id}")])
    rows.append([types.InlineKeyboardButton(
        text=_t(lang, "back"),
        callback_data=_back_to_list(callback.from_user.id, "shop:local_list"),
    )])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@catalog_router.callback_query(F.data.startswith("shop:sprod:"))
async def on_supplier_detail(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        product_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    try:
        async with _session() as session:
            product = await session.get(SupplierProduct, product_id)
            supplier_name = None
            if product is not None:
                supplier = await session.get(Supplier, product.supplier_id)
                supplier_name = (supplier.display_name or supplier.name) if supplier else None
    except Exception:
        logger.exception("SubWorld supplier detail failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    if product is None or not product.is_visible:
        await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    buyable = _supplier_buyable(product)
    dot = _supplier_dot(product)
    lines = [f"<b>{product.name}</b>"]
    if product.description:
        lines.append(product.description[:800])
    lines.append(f"{_t(lang, 'price')}: {_fmt_price(product.selling_price or 0)}")
    stock = _t(lang, "unlimited") if product.is_unlimited else str(product.stock or 0)
    stock_line = f"{dot} {_t(lang, 'stock')}: {stock}"
    if not buyable:
        stock_line += f" — {_t(lang, 'out_of_stock')}"
    lines.append(stock_line)
    if supplier_name:
        lines.append(f"🏬 {supplier_name}")
    rows = []
    if buyable:
        rows.append([types.InlineKeyboardButton(text=_t(lang, "buy"), callback_data=f"shop:buy:sup:{product.id}")])
    rows.append([types.InlineKeyboardButton(
        text=_t(lang, "back"),
        callback_data=_back_to_list(callback.from_user.id, f"shop:sup:{product.supplier_id}"),
    )])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


# ============================================================
#  Buying from the wallet balance
# ============================================================

def _insufficient_text(lang: str, balance: Decimal, price: Decimal) -> str:
    return (
        f"{_t(lang, 'not_enough')}\n"
        f"{_t(lang, 'balance_line')}: {_fmt_price(balance)}\n"
        f"{_t(lang, 'price')}: {_fmt_price(price)}\n\n"
        f"{_t(lang, 'topup_hint')}"
    )


@catalog_router.callback_query(F.data.startswith("shop:buy:"))
async def on_buy(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, kind, raw_id = parts
    try:
        product_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return
    back_target = "shop:local_list" if kind == "local" else "menu:shop"
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, back_target))
        await callback.answer()
        return
    try:
        async with _session() as session:
            user = await _get_user(session, callback.from_user.id, lang)
            balance = Decimal(str(user.balance_usd or 0))
            if kind == "local":
                product = await session.get(Product, product_id)
                if product is None or not product.is_active:
                    await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target))
                    await callback.answer()
                    return
                if (
                    (product.delivery_type or "codes") != "codes"
                    and product.quantity is not None
                    and product.quantity <= 0
                ):
                    await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target))
                    await callback.answer()
                    return
                price = Decimal(str(product.price_usd))
                name = product.name_en if lang == "en" else product.name_ar
                if balance < price:
                    await callback.message.edit_text(
                        _insufficient_text(lang, balance, price),
                        reply_markup=_back_kb(lang, back_target),
                    )
                    await callback.answer()
                    return
                code_row = None
                if (product.delivery_type or "codes") == "codes":
                    code_row = (await session.execute(
                        select(ProductCode).where(
                            ProductCode.product_id == product_id,
                            ProductCode.is_used == False,  # noqa: E712
                        ).order_by(ProductCode.id).limit(1)
                    )).scalars().first()
                    if code_row is None:
                        await callback.message.edit_text(_t(lang, "no_codes"), reply_markup=_back_kb(lang, back_target))
                        await callback.answer()
                        return
                user.balance_usd = balance - price
                order = Order(
                    user_id=callback.from_user.id,
                    product_id=product_id,
                    amount_usd=price,
                    status="delivered" if code_row is not None else "pending",
                )
                session.add(order)
                await session.flush()
                delivered_code = None
                if code_row is not None:
                    code_row.is_used = True
                    code_row.used_by_user_id = callback.from_user.id
                    code_row.used_at = datetime.now(timezone.utc)
                    order.code_id = code_row.id
                    delivered_code = code_row.code
                await session.commit()
                new_balance = balance - price
                if delivered_code is not None:
                    text = (
                        f"{_t(lang, 'bought')}\n"
                        f"{name}\n"
                        f"{_t(lang, 'price')}: {_fmt_price(price)}\n"
                        f"{_t(lang, 'remaining')}: {_fmt_price(new_balance)}\n\n"
                        f"{_t(lang, 'your_code')}:\n"
                        f"<code>{delivered_code}</code>"
                    )
                else:
                    text = (
                        f"{_t(lang, 'order_created')}\n"
                        f"{name}\n"
                        f"{_t(lang, 'price')}: {_fmt_price(price)}\n"
                        f"{_t(lang, 'remaining')}: {_fmt_price(new_balance)}"
                    )
                await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:shop"))
                await callback.answer()
                return
            # Supplier product: pending order, admin delivers.
            product = await session.get(SupplierProduct, product_id)
            if product is None or not product.is_visible or not _supplier_buyable(product):
                await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target))
                await callback.answer()
                return
            price = Decimal(str(product.selling_price or 0))
            if balance < price:
                await callback.message.edit_text(
                    _insufficient_text(lang, balance, price),
                    reply_markup=_back_kb(lang, back_target),
                )
                await callback.answer()
                return
            user.balance_usd = balance - price
            order = Order(
                user_id=callback.from_user.id,
                supplier_product_id=product_id,
                amount_usd=price,
                status="pending",
            )
            session.add(order)
            await session.commit()
            logger.info(
                "SubWorld supplier order %s: user %s bought %s for $%s (pending delivery)",
                order.id, callback.from_user.id, product.name, price,
            )
            text = (
                f"{_t(lang, 'order_created')}\n"
                f"{product.name}\n"
                f"{_t(lang, 'price')}: {_fmt_price(price)}\n"
                f"{_t(lang, 'remaining')}: {_fmt_price(balance - price)}"
            )
            await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:shop"))
            await callback.answer()
            return
    except Exception:
        logger.exception("SubWorld buy failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, back_target))
        await callback.answer()
        return
