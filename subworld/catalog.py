"""SubWorld store catalog: browse local products and supplier products.

Prices are USD only. Buying pays from the wallet balance:
- local "codes" products deliver a code immediately;
- supplier / invite / manual products create a pending order for the admin.
Wallet top-up (Binance/crypto proof + admin approval) is the next step;
until then a purchase with insufficient balance explains how to top up.

Store lists are paginated (10 products per page) with next/previous
buttons. Supplier stores first show their categories; a product moved
to Featured in the admin panel (show_in_local) appears in the Featured
list instead of its supplier store. Availability is shown by the
button colour (green when buyable, red when out of stock, Bot API 9.4
styles) and the quantity is shown on every product button - no dots.
Headers carry a divider line (like the Yemen bot's welcome) so the
message bubble - and the buttons attached to it - stretch to full width.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import F, Router, types
from sqlalchemy import func, select

from .button_models import DEFAULT_LABELS, BotButton, label_text, load_labels
from .db import get_session_factory
from .keyboards import CHOOSE_TEXT, DIVIDER, TEXTS, choose_kb, get_lang, main_menu_kb
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
            "ar": "🛍️ المتجر\n" + DIVIDER + "\nاختر القسم:",
            "en": "🛍️ Store\n" + DIVIDER + "\nChoose a section:",
        },
        "local_section": {"ar": "⭐ الاشتراكات المميزة", "en": "⭐ Featured subscriptions"},
        "local_title": {"ar": "⭐ الاشتراكات المميزة:\n" + DIVIDER, "en": "⭐ Featured subscriptions:\n" + DIVIDER},
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


async def _safe_edit(callback: types.CallbackQuery, text: str, reply_markup) -> None:
    """Edit a message, silently ignoring 'message is not modified'
    (double-tap, or Back to the screen already shown). Any other
    Telegram error is re-raised so real failures stay visible."""
    from aiogram.exceptions import TelegramBadRequest

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
        logger.debug("SubWorld edit skipped: message not modified")


def _supplier_buyable(product: SupplierProduct) -> bool:
    """True when the product can actually be bought right now."""
    if not product.is_available:
        return False
    if product.is_unlimited:
        return True
    return (product.stock or 0) > 0


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


async def _labels() -> dict:
    """Button labels from the panel (BotLabel rows), with hardcoded
    defaults when the table is empty or unreadable."""
    try:
        async with _session() as session:
            return await load_labels(session)
    except Exception:
        logger.exception("SubWorld labels read failed")
        return dict(DEFAULT_LABELS)


def _supplier_stock_text(product: SupplierProduct) -> str:
    """Stock shown on a product button: the number, or ∞ when unlimited."""
    return "∞" if product.is_unlimited else str(product.stock or 0)


def _back_kb(lang: str, target: str, labels: dict | None = None) -> types.InlineKeyboardMarkup:
    labels = labels or DEFAULT_LABELS
    back_text, back_style = label_text(labels, "back", lang)
    menu_text, menu_style = label_text(labels, "main_menu", lang)
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=back_text, callback_data=target, style=back_style)],
            [types.InlineKeyboardButton(text=menu_text, callback_data="menu:main", style=menu_style)],
        ]
    )


def _nav_row(lang: str, page: int, pages: int, page_cb, labels: dict | None = None) -> list:
    """Previous / Next buttons for the current page (one row, may be empty)."""
    labels = labels or DEFAULT_LABELS
    row = []
    if page > 1:
        text, style = label_text(labels, "prev", lang)
        row.append(types.InlineKeyboardButton(text=text, callback_data=page_cb(page - 1), style=style))
    if page < pages:
        text, style = label_text(labels, "next", lang)
        row.append(types.InlineKeyboardButton(text=text, callback_data=page_cb(page + 1), style=style))
    return [row] if row else []


# ============================================================
#  Main menu / balance / language
# ============================================================

@catalog_router.callback_query(F.data == "menu:main")
async def on_menu_main(callback: types.CallbackQuery) -> None:
    # The main menu is an inline keyboard under the welcome message:
    # turn this message back into the welcome + menu.
    lang = get_lang(callback.from_user.id)
    from .buttons import edit_main_menu

    await edit_main_menu(callback, lang)
    await callback.answer()


@catalog_router.callback_query(F.data.in_({"menu:lang", "choose_language"}))
async def on_menu_lang(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(CHOOSE_TEXT, reply_markup=choose_kb())
    await callback.answer()


@catalog_router.callback_query(F.data == "rules")
async def on_rules(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    from .buttons import _send_rules

    await _send_rules(callback, lang)


def _balance_text(balance: Decimal, lang: str) -> str:
    return (
        f"{_t(lang, 'balance_title')}\n{DIVIDER}\n"
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


@catalog_router.callback_query(F.data.in_({"menu:balance", "profile"}))
async def on_menu_balance(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    labels = await _labels()
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main", labels))
        await callback.answer()
        return
    balance = await _load_balance(callback.from_user.id, lang)
    if balance is None:
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main", labels))
        await callback.answer()
        return
    await callback.message.edit_text(
        _balance_text(balance, lang), reply_markup=_back_kb(lang, "menu:main", labels)
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


def _store_sections_kb(lang: str, suppliers, labels: dict | None = None) -> types.InlineKeyboardMarkup:
    labels = labels or DEFAULT_LABELS
    buttons = [[types.InlineKeyboardButton(text=_t(lang, "local_section"), callback_data="shop:local_list", style="primary")]]
    for sup in suppliers:
        label = (sup.display_name or sup.name or f"Supplier {sup.id}")[:40]
        buttons.append([types.InlineKeyboardButton(text=f"🏬 {label}", callback_data=f"shop:sup:{sup.id}", style="primary")])
    menu_text, menu_style = label_text(labels, "main_menu", lang)
    buttons.append([types.InlineKeyboardButton(text=menu_text, callback_data="menu:main", style=menu_style)])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


@catalog_router.callback_query(F.data.in_({"menu:shop", "shop"}))
async def on_menu_shop(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    labels = await _labels()
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main", labels))
        await callback.answer()
        return
    try:
        async with _session() as session:
            suppliers = await _load_store_suppliers(session)
    except Exception:
        logger.exception("SubWorld store sections failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main", labels))
        await callback.answer()
        return
    await callback.message.edit_text(
        _t(lang, "store_title"),
        reply_markup=_store_sections_kb(lang, suppliers, labels),
    )
    await callback.answer()


# ============================================================
#  Featured subscriptions (local products + products moved here
#  from a supplier via show_in_local in the admin panel)
# ============================================================

async def _render_local_list(callback: types.CallbackQuery, page: int) -> None:
    lang = get_lang(callback.from_user.id)
    labels = await _labels()
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
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop", labels))
        await callback.answer()
        return
    items = []  # (label, callback_data, style)
    for p in products:
        name = (p.name_en if lang == "en" else p.name_ar)[:28]
        if (p.delivery_type or "codes") == "codes":
            stock = codes_left.get(p.id, 0)
            buyable = stock > 0
            stock_text = str(stock)
        else:
            buyable = p.quantity is None or p.quantity > 0
            stock_text = "∞" if p.quantity is None else str(p.quantity)
        style = "success" if buyable else "danger"
        items.append((f"{name} — {_fmt_price(p.price_usd)} (📦{stock_text})", f"shop:local:{p.id}", style))
    for p in moved:
        buyable = _supplier_buyable(p)
        items.append((
            f"{p.name[:28]} — {_fmt_price(p.selling_price or 0)} (📦{_supplier_stock_text(p)})",
            f"shop:sprod:{p.id}",
            "success" if buyable else "danger",
        ))
    if not items:
        await callback.message.edit_text(_t(lang, "empty"), reply_markup=_back_kb(lang, "menu:shop", labels))
        await callback.answer()
        return
    page_items, pages, page = _paginate(items, page)
    list_cb = f"shop:feat:{page}" if page > 1 else "shop:local_list"
    _remember_list(callback.from_user.id, list_cb)
    buttons = [
        [types.InlineKeyboardButton(text=label, callback_data=cb, style=style)]
        for label, cb, style in page_items
    ]
    buttons += _nav_row(lang, page, pages, lambda p: f"shop:feat:{p}", labels)
    back_text, back_style = label_text(labels, "back", lang)
    buttons.append([types.InlineKeyboardButton(text=back_text, callback_data="menu:shop", style=back_style)])
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
    labels = await _labels()
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
        await _safe_edit(callback, _t(lang, "store_unavailable"), _back_kb(lang, "menu:shop", labels))
        await callback.answer()
        return
    title = (supplier.display_name or supplier.name) if supplier else f"Supplier {supplier_id}"
    if total == 0:
        await _safe_edit(callback, f"🏬 {title}\n{_t(lang, 'empty')}", _back_kb(lang, "menu:shop", labels))
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
            style="primary",
        )])
    all_text, all_style = label_text(labels, "all_products", lang)
    buttons.append([types.InlineKeyboardButton(
        text=all_text,
        callback_data=f"shop:supa:{supplier_id}:1",
        style=all_style,
    )])
    back_text, back_style = label_text(labels, "back", lang)
    buttons.append([types.InlineKeyboardButton(text=back_text, callback_data="menu:shop", style=back_style)])
    await _safe_edit(
        callback,
        f"🏬 {title}\n{DIVIDER}\n{_t(lang, 'categories')}",
        types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


async def _render_supplier_products(
    callback: types.CallbackQuery, supplier_id: int, cat_idx: int | None, page: int
) -> None:
    lang = get_lang(callback.from_user.id)
    labels = await _labels()
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
        await _safe_edit(callback, _t(lang, "store_unavailable"), _back_kb(lang, "menu:shop", labels))
        await callback.answer()
        return
    title = (supplier.display_name or supplier.name) if supplier else f"Supplier {supplier_id}"
    if cat is not None:
        title += f" — {cat}"
    elif cat_idx is not None:
        title += f" — {_t(lang, 'other_category')}"
    # Back goes to the category screen only when one exists; a store
    # with one category (or none) has no category screen, so Back
    # returns to the store sections - otherwise it re-renders this
    # same list and Telegram refuses ("message is not modified").
    back_cb = f"shop:sup:{supplier_id}" if len(cats) > 1 else "menu:shop"
    if not rows:
        await _safe_edit(callback, f"🏬 {title}\n{_t(lang, 'empty')}", _back_kb(lang, back_cb, labels))
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
            text=f"{p.name[:28]} — {_fmt_price(p.selling_price or 0)} (📦{_supplier_stock_text(p)})",
            callback_data=f"shop:sprod:{p.id}",
            style="success" if _supplier_buyable(p) else "danger",
        )]
        for p in page_rows
    ]
    buttons += _nav_row(lang, page, pages, page_cb, labels)
    back_text, back_style = label_text(labels, "back", lang)
    buttons.append([types.InlineKeyboardButton(text=back_text, callback_data=back_cb, style=back_style)])
    text = f"🏬 {title}:\n{DIVIDER}"
    if pages > 1:
        text += f"\n{_t(lang, 'page')} {page} {_t(lang, 'of')} {pages}"
    await _safe_edit(callback, text, types.InlineKeyboardMarkup(inline_keyboard=buttons))
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
    labels = await _labels()
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
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "shop:local_list", labels))
        await callback.answer()
        return
    if product is None or not product.is_active:
        await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, "shop:local_list", labels))
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
    elif (product.delivery_type or "codes") != "codes":
        stock = _t(lang, "unlimited") if product.quantity is None else str(product.quantity)
        lines.append(f"{_t(lang, 'stock')}: {stock}")
    if (product.delivery_type or "codes") == "codes":
        buyable = (codes_left or 0) > 0
    else:
        buyable = product.quantity is None or product.quantity > 0
    buy_text, buy_style = label_text(labels, "buy", lang)
    back_text, back_style = label_text(labels, "back", lang)
    rows = []
    if buyable:
        rows.append([types.InlineKeyboardButton(text=buy_text, callback_data=f"shop:buy:local:{product.id}", style=buy_style)])
    rows.append([types.InlineKeyboardButton(
        text=back_text,
        callback_data=_back_to_list(callback.from_user.id, "shop:local_list"),
        style=back_style,
    )])
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@catalog_router.callback_query(F.data.startswith("shop:sprod:"))
async def on_supplier_detail(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    labels = await _labels()
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
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop", labels))
        await callback.answer()
        return
    if product is None or not product.is_visible:
        await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, "menu:shop", labels))
        await callback.answer()
        return
    buyable = _supplier_buyable(product)
    lines = [f"<b>{product.name}</b>"]
    if product.description:
        lines.append(product.description[:800])
    lines.append(f"{_t(lang, 'price')}: {_fmt_price(product.selling_price or 0)}")
    stock = _t(lang, "unlimited") if product.is_unlimited else str(product.stock or 0)
    stock_line = f"{_t(lang, 'stock')}: {stock}"
    if not buyable:
        stock_line += f" — {_t(lang, 'out_of_stock')}"
    lines.append(stock_line)
    if supplier_name:
        lines.append(f"🏬 {supplier_name}")
    buy_text, buy_style = label_text(labels, "buy", lang)
    back_text, back_style = label_text(labels, "back", lang)
    rows = []
    if buyable:
        rows.append([types.InlineKeyboardButton(text=buy_text, callback_data=f"shop:buy:sup:{product.id}", style=buy_style)])
    rows.append([types.InlineKeyboardButton(
        text=back_text,
        callback_data=_back_to_list(callback.from_user.id, f"shop:sup:{product.supplier_id}"),
        style=back_style,
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
    labels = await _labels()
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
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, back_target, labels))
        await callback.answer()
        return
    try:
        async with _session() as session:
            user = await _get_user(session, callback.from_user.id, lang)
            balance = Decimal(str(user.balance_usd or 0))
            if kind == "local":
                product = await session.get(Product, product_id)
                if product is None or not product.is_active:
                    await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target, labels))
                    await callback.answer()
                    return
                if (
                    (product.delivery_type or "codes") != "codes"
                    and product.quantity is not None
                    and product.quantity <= 0
                ):
                    await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target, labels))
                    await callback.answer()
                    return
                price = Decimal(str(product.price_usd))
                name = product.name_en if lang == "en" else product.name_ar
                if balance < price:
                    await callback.message.edit_text(
                        _insufficient_text(lang, balance, price),
                        reply_markup=_back_kb(lang, back_target, labels),
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
                        await callback.message.edit_text(_t(lang, "no_codes"), reply_markup=_back_kb(lang, back_target, labels))
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
                await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:shop", labels))
                await callback.answer()
                return
            # Supplier product: pending order, admin delivers.
            product = await session.get(SupplierProduct, product_id)
            if product is None or not product.is_visible or not _supplier_buyable(product):
                await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target, labels))
                await callback.answer()
                return
            price = Decimal(str(product.selling_price or 0))
            if balance < price:
                await callback.message.edit_text(
                    _insufficient_text(lang, balance, price),
                    reply_markup=_back_kb(lang, back_target, labels),
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
            await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:shop", labels))
            await callback.answer()
            return
    except Exception:
        logger.exception("SubWorld buy failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, back_target, labels))
        await callback.answer()
        return


# ============================================================
#  Panel buttons with no handler yet
# ============================================================

@catalog_router.callback_query()
async def on_unwired_button(callback: types.CallbackQuery) -> None:
    """Last resort, registered after every other catalog handler: a
    panel button whose callback_data has no handler (e.g. a support
    button with no link). Answer with a toast telling the user what is
    missing, instead of leaving the button dead. Callbacks that are
    not panel buttons are passed on to the next handler."""
    from aiogram.dispatcher.event.bases import SkipHandler

    data = (callback.data or "").strip()
    try:
        async with _session() as session:
            found = (
                await session.execute(
                    select(BotButton.id)
                    .where(BotButton.callback_data == data)
                    .limit(1)
                )
            ).scalars().first()
    except Exception:
        logger.exception("SubWorld unwired-button lookup failed")
        found = None
    if found is None:
        raise SkipHandler
    lang = get_lang(callback.from_user.id)
    await callback.answer(
        "هذا الزر غير مفعّل بعد — أضف رابطه من لوحة التحكم."
        if lang == "ar"
        else "This button is not wired yet — add its link from the admin panel."
    )
