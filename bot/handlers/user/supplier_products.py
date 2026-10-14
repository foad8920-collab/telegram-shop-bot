"""Supplier products browser  dynamic categories from DB."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, distinct

from bot.handlers.user.shop_and_goods import _shorten
from bot.database.main import Database
from bot.database.models.main import Supplier, SupplierProduct


logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)


class SupplierSearch(StatesGroup):
    waiting_query = State()

_CATS_PER_PAGE = 12
_PRODS_PER_PAGE = 10


async def _safe_answer(call: CallbackQuery):
    try:
        await call.answer()
    except TelegramBadRequest:
        pass


def _fmt(value) -> str:
    try:
        return f"{Decimal(str(value)).quantize(Decimal('0.01'))}" + chr(36)
    except Exception:
        return f"{value}$"


# ============================================================
#  Category list
# ============================================================

async def _build_categories_kb(
    supplier_id: int,
    cat_page: int = 0,
    uncat_page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    """Dynamic mix + dual pagination (categories + uncategorized)."""
    from sqlalchemy import func as sa_func, or_ as sa_or

    async with Database().session() as session:
        supplier = await session.get(Supplier, supplier_id)
        if supplier is None:
            return " المورد غير موجود.", InlineKeyboardMarkup(inline_keyboard=[])

        # Counts
        total_cats_result = await session.execute(
            select(sa_func.count(sa_func.distinct(SupplierProduct.category)))
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.is_visible == True,
                SupplierProduct.category.isnot(None),
                SupplierProduct.category != "",
            )
        )
        total_cats = int(total_cats_result.scalar() or 0)

        uncat_count_result = await session.execute(
            select(sa_func.count(SupplierProduct.id))
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.is_visible == True,
                sa_or(
                    SupplierProduct.category.is_(None),
                    SupplierProduct.category == "",
                ),
            )
        )
        uncat_total = int(uncat_count_result.scalar() or 0)

        # Dynamic limits
        if total_cats == 0:
            CATS_PER_PAGE = 0
            UNCAT_PER_PAGE = 40
        elif total_cats <= 10:
            CATS_PER_PAGE = 10
            UNCAT_PER_PAGE = 30
        elif total_cats <= 30:
            CATS_PER_PAGE = 15
            UNCAT_PER_PAGE = 20
        else:
            CATS_PER_PAGE = 30
            UNCAT_PER_PAGE = 15

        # Fetch all categories (for pagination)
        all_cats: list[tuple[str, int]] = []
        if total_cats > 0:
            cat_result = await session.execute(
                select(
                    SupplierProduct.category,
                    sa_func.count(SupplierProduct.id).label("cnt"),
                )
                .where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.is_visible == True,
                    SupplierProduct.category.isnot(None),
                    SupplierProduct.category != "",
                )
                .group_by(SupplierProduct.category)
                .order_by(sa_func.count(SupplierProduct.id).desc())
            )
            all_cats = [(r[0], r[1]) for r in cat_result if r[0]]

        # Paginate categories
        total_cat_pages = max(1, (len(all_cats) + CATS_PER_PAGE - 1) // CATS_PER_PAGE) if CATS_PER_PAGE > 0 else 1
        if cat_page < 0:
            cat_page = 0
        if cat_page >= total_cat_pages:
            cat_page = total_cat_pages - 1

        if CATS_PER_PAGE > 0:
            cat_start = cat_page * CATS_PER_PAGE
            page_cats = all_cats[cat_start:cat_start + CATS_PER_PAGE]
        else:
            page_cats = []

        # Paginate uncategorized
        total_uncat_pages = max(1, (uncat_total + UNCAT_PER_PAGE - 1) // UNCAT_PER_PAGE) if uncat_total > 0 else 1
        if uncat_page < 0:
            uncat_page = 0
        if uncat_page >= total_uncat_pages:
            uncat_page = total_uncat_pages - 1

        uncat_offset = uncat_page * UNCAT_PER_PAGE
        uncat_result = await session.execute(
            select(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.is_visible == True,
                sa_or(
                    SupplierProduct.category.is_(None),
                    SupplierProduct.category == "",
                ),
            )
            .order_by(
                SupplierProduct.is_available.desc(),
                SupplierProduct.stock.desc(),
                SupplierProduct.id.desc(),
            )
            .offset(uncat_offset)
            .limit(UNCAT_PER_PAGE)
        )
        uncat_products = list(uncat_result.scalars().all())

    # ---- Build keyboard ----
    rows: list[list[InlineKeyboardButton]] = []

    # Categories (2 per row)
    if page_cats:
        current_row: list[InlineKeyboardButton] = []
        for cat, cnt in page_cats:
            label = f" {cat[:20]} ({cnt})"
            current_row.append(InlineKeyboardButton(
                text=label,
                callback_data=f"sv_cat:{supplier_id}:{cat}",
                style="primary",
            ))
            if len(current_row) == 2:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)

    # Categories pagination (always show if more than 1 page)
    if total_cat_pages > 1:
        nav: list[InlineKeyboardButton] = []

        # Previous
        if cat_page > 0:
            nav.append(InlineKeyboardButton(
                text=" السابق",
                callback_data=f"sv_cats_page:{supplier_id}:{cat_page - 1}:{uncat_page}",
                style="primary",
            ))
        else:
            nav.append(InlineKeyboardButton(
                text=" السابق",
                callback_data="noop",
                style="primary",
            ))

        # Page indicator
        nav.append(InlineKeyboardButton(
            text=f" {cat_page + 1}/{total_cat_pages}",
            callback_data="noop",
            style="primary",
        ))

        # Next
        if cat_page < total_cat_pages - 1:
            nav.append(InlineKeyboardButton(
                text="التالي ",
                callback_data=f"sv_cats_page:{supplier_id}:{cat_page + 1}:{uncat_page}",
                style="primary",
            ))
        else:
            nav.append(InlineKeyboardButton(
                text="التالي ",
                callback_data="noop",
                style="primary",
            ))

        rows.append(nav)

    # Uncategorized products (1 per row)
    for prod in uncat_products:
        stock = int(prod.stock or 0)
        available = prod.is_available and (prod.is_unlimited or stock > 0)

        if available:
            icon = ""
            stock_text = "" if prod.is_unlimited else str(stock)
            style = "success"
            suffix = f" ({stock_text})"
        else:
            icon = ""
            style = "danger"
            suffix = " (نفدت)"

        name = prod.name or ""
        price = _fmt(prod.selling_price) if prod.selling_price else "?"

        rows.append([InlineKeyboardButton(
            text=f"{icon} {name}  {price}{suffix}",
            callback_data=f"sv_prod:{supplier_id}:{prod.id}",
            style=style,
        )])

    # Uncategorized pagination
    if total_uncat_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if uncat_page > 0:
            nav.append(InlineKeyboardButton(
                text=" السابق",
                callback_data=f"sv_uncat_page:{supplier_id}:{cat_page}:{uncat_page - 1}",
                style="primary",
            ))
        nav.append(InlineKeyboardButton(
            text=f" {uncat_page + 1}/{total_uncat_pages}",
            callback_data="noop",
            style="primary",
        ))
        if uncat_page < total_uncat_pages - 1:
            nav.append(InlineKeyboardButton(
                text="التالي ",
                callback_data=f"sv_uncat_page:{supplier_id}:{cat_page}:{uncat_page + 1}",
                style="primary",
            ))
        rows.append(nav)

    rows.append([InlineKeyboardButton(
        text=" البحث في المنتجات",
        callback_data=f"sv_search:{supplier_id}",
        style="primary",
    )])
    rows.append([InlineKeyboardButton(text=" رجوع", callback_data="shop", style="primary")])

    # ---- Text ----
    icon = supplier.icon_emoji or ""
    name = supplier.display_name or supplier.name

    lines = [f"{icon} <b>{name}</b>", ""]

    if page_cats:
        if total_cats > len(all_cats):
            lines.append(f" الفئات ({len(all_cats)}  صفحة {cat_page + 1}/{total_cat_pages}):")
        else:
            lines.append(f" الفئات ({len(all_cats)}):")

    if uncat_products:
        lines.append("")
        if total_uncat_pages > 1:
            lines.append(f" بدون تصنيف ({uncat_total})  صفحة {uncat_page + 1}/{total_uncat_pages}:")
        else:
            lines.append(f" بدون تصنيف ({uncat_total}):")

    text = NL.join(lines)
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("shop_supplier_"))
async def supplier_categories_handler(call: CallbackQuery, state: FSMContext) -> None:
    """Show categories for a supplier (when callback is shop_supplier_<id>)."""
    await _safe_answer(call)

    # Ignore if this is a shop_supplier_<id> handled elsewhere
    parts = (call.data or "").split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        return

    supplier_id = int(parts[2])
    text, kb = await _build_categories_kb(supplier_id, cat_page=0, uncat_page=0)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("sv_cats_page:"))
async def supplier_categories_page_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    # Format: sv_cats_page:{sid}:{cat_page}:{uncat_page}
    try:
        parts = call.data.split(":")
        supplier_id = int(parts[1])
        cat_page = int(parts[2])
        uncat_page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError, AttributeError):
        return
    text, kb = await _build_categories_kb(supplier_id, cat_page=cat_page, uncat_page=uncat_page)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("sv_uncat_page:"))
async def supplier_uncat_page_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    # Format: sv_uncat_page:{sid}:{cat_page}:{uncat_page}
    try:
        parts = call.data.split(":")
        supplier_id = int(parts[1])
        cat_page = int(parts[2])
        uncat_page = int(parts[3])
    except (ValueError, IndexError, AttributeError):
        return
    text, kb = await _build_categories_kb(supplier_id, cat_page=cat_page, uncat_page=uncat_page)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


# ============================================================
#  Products in a category
# ============================================================

async def _build_products_kb(supplier_id: int, category: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with Database().session() as session:
        supplier = await session.get(Supplier, supplier_id)
        if supplier is None:
            return " المورد غير موجود.", InlineKeyboardMarkup(inline_keyboard=[])

        result = await session.execute(
            select(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.category == category,
                SupplierProduct.is_visible == True,
            )
            .order_by(SupplierProduct.is_available.desc(), SupplierProduct.selling_price.asc())
        )
        products = list(result.scalars().all())

    if not products:
        text = NL.join([
            f" <b>{category}</b>",
            "",
            " لا توجد منتجات في هذه الفئة.",
        ])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع للفئات", callback_data=f"shop_supplier_{supplier_id}", style="primary")],
        ])
        return text, kb

    total_pages = max(1, (len(products) + _PRODS_PER_PAGE - 1) // _PRODS_PER_PAGE)
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * _PRODS_PER_PAGE
    page_prods = products[start:start + _PRODS_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for prod in page_prods:
        stock = int(prod.stock or 0)
        available = prod.is_available and (prod.is_unlimited or stock > 0)

        if available:
            icon = ""
            if prod.is_unlimited:
                stock_text = ""
            else:
                stock_text = f"{stock}"
            style = "success"
            suffix = f" ({stock_text})"
        else:
            icon = ""
            style = "danger"
            suffix = " (نفدت)"

        name = prod.name or ""[:35]
        price = _fmt(prod.selling_price) if prod.selling_price else "?"
        text = f"{icon} {name}  {price}{suffix}"

        rows.append([InlineKeyboardButton(
            text=text,
            callback_data=f"sv_prod:{supplier_id}:{prod.id}",
            style=style,
        )])

    # Pagination (only if more than one page)
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text=" السابق",
                callback_data=f"sv_prods_page:{supplier_id}:{category}:{page - 1}",
                style="primary",
            ))
        nav.append(InlineKeyboardButton(
            text=f" {page + 1}/{total_pages}",
            callback_data="noop",
            style="primary",
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="التالي ",
                callback_data=f"sv_prods_page:{supplier_id}:{category}:{page + 1}",
                style="primary",
            ))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=" رجوع للفئات", callback_data=f"shop_supplier_{supplier_id}", style="primary")])

    text = NL.join([
        f" <b>{category}</b>",
        "",
        f" {len(products)} منتج  الصفحة {page + 1}/{total_pages}",
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sv_cat:"))
async def supplier_category_products_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    # Format: sv_cat:{sid}:{category}
    try:
        parts = call.data.split(":", 2)
        supplier_id = int(parts[1])
        category = parts[2]
    except (ValueError, IndexError, AttributeError):
        return
    text, kb = await _build_products_kb(supplier_id, category, page=0)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("sv_prods_page:"))
async def supplier_products_page_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    # Format: sv_prods_page:{sid}:{category}:{page}
    try:
        parts = call.data.split(":", 3)
        supplier_id = int(parts[1])
        category = parts[2]
        page = int(parts[3])
    except (ValueError, IndexError, AttributeError):
        return
    text, kb = await _build_products_kb(supplier_id, category, page=page)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


# ============================================================
#  Product details
# ============================================================

@router.callback_query(F.data.startswith("sv_prod:"))
async def supplier_product_detail_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    # Format: sv_prod:{sid}:{product_id}
    try:
        _, sid_s, pid_s = call.data.split(":")
        supplier_id = int(sid_s)
        product_id = int(pid_s)
    except (ValueError, AttributeError):
        return

    async with Database().session() as session:
        prod = await session.get(SupplierProduct, product_id)
        if prod is None or prod.supplier_id != supplier_id:
            await call.answer(" المنتج غير موجود.", show_alert=True)
            return

    name = prod.name or ""
    price = _fmt(prod.selling_price) if prod.selling_price else "?"
    available = prod.is_available and (prod.is_unlimited or (prod.stock or 0) > 0)

    if available:
        stock_text = "غير محدود" if prod.is_unlimited else str(prod.stock or 0)
        status = f" <b>متوفر</b> (المخزون: {stock_text})"
    else:
        status = " <b>نفدت الكمية</b>"

    desc = (prod.description or "").strip()
    if len(desc) > 500:
        desc = desc[:500] + "..."

    lines = [
        f" <b>{name}</b>",
        "",
    ]
    if desc:
        lines.append(f" {desc}")
        lines.append("")
    lines.append(f" السعر: <b>{price}</b>")
    lines.append(f" {status}")

    text = NL.join(lines)

    # Back destination: category (if exists) or supplier main page
    if prod.category and prod.category.strip():
        back_cb = f"sv_cat:{supplier_id}:{prod.category}"
    else:
        back_cb = f"shop_supplier_{supplier_id}"

    if available:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" شراء الآن", callback_data=f"sv_buy:{supplier_id}:{product_id}", style="success")],
            [InlineKeyboardButton(text=" رجوع", callback_data=back_cb, style="primary")],
        ])
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع", callback_data=back_cb, style="primary")],
        ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery) -> None:
    await _safe_answer(call)




@router.callback_query(F.data.startswith("sv_search:"))
async def supplier_search_start_handler(call: CallbackQuery, state: FSMContext) -> None:
    """Start search  ask for query."""
    await _safe_answer(call)

    try:
        supplier_id = int(call.data.split(":")[1])
    except (ValueError, IndexError, AttributeError):
        return

    async with Database().session() as session:
        supplier = await session.get(Supplier, supplier_id)
        if supplier is None:
            await call.answer(" المورد غير موجود.", show_alert=True)
            return

    name = supplier.display_name or supplier.name

    await state.update_data(search_supplier_id=supplier_id)
    await state.set_state(SupplierSearch.waiting_query)

    text = NL.join([
        f" <b>البحث في {name}</b>",
        "",
        " <b>أرسل كلمة للبحث عنها:</b>",
        "",
        "<i>مثال: Netflix, ChatGPT, Adobe, VPN...</i>",
        "",
        " للإلغاء أرسل: <code>/cancel</code>",
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=" إلغاء",
            callback_data=f"shop_supplier_{supplier_id}",
            style="danger",
        )],
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(SupplierSearch.waiting_query, F.text)
async def supplier_search_message_handler(message: Message, state: FSMContext) -> None:
    """Receive query, show results."""
    query = (message.text or "").strip()

    if query == "/cancel":
        await state.clear()
        await message.answer(" تم الإلغاء.")
        return

    if len(query) < 2:
        await message.answer(" الكلمة قصيرة جداً. أرسل كلمة أطول.")
        return

    if len(query) > 50:
        await message.answer(" الكلمة طويلة جداً (الحد 50 حرف).")
        return

    data = await state.get_data()
    supplier_id = data.get("search_supplier_id")

    if not supplier_id:
        await message.answer(" انتهت الجلسة.")
        await state.clear()
        return

    await state.clear()

    from sqlalchemy import func as sa_func

    async with Database().session() as session:
        result = await session.execute(
            select(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.is_visible == True,
                sa_func.lower(SupplierProduct.name).like(f"%{query.lower()}%"),
            )
            .order_by(
                SupplierProduct.is_available.desc(),
                SupplierProduct.stock.desc(),
                SupplierProduct.selling_price.asc(),
            )
            .limit(30)
        )
        products = list(result.scalars().all())

    if not products:
        text = NL.join([
            f" <b>البحث عن: {query}</b>",
            "",
            " لا توجد نتائج.",
            " جرّب كلمة أخرى.",
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" بحث جديد", callback_data=f"sv_search:{supplier_id}", style="primary")],
            [InlineKeyboardButton(text=" رجوع", callback_data=f"shop_supplier_{supplier_id}", style="primary")],
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=markup)
        return

    rows: list[list[InlineKeyboardButton]] = []
    for prod in products:
        stock = int(prod.stock or 0)
        available = prod.is_available and (prod.is_unlimited or stock > 0)

        if available:
            icon = ""
            stock_text = "" if prod.is_unlimited else str(stock)
            style = "success"
            suffix = f" ({stock_text})"
        else:
            icon = ""
            style = "danger"
            suffix = " (نفدت)"

        name = prod.name or ""[:35]
        price = _fmt(prod.selling_price) if prod.selling_price else "?"

        rows.append([InlineKeyboardButton(
            text=f"{icon} {name}  {price}{suffix}",
            callback_data=f"sv_prod:{supplier_id}:{prod.id}",
            style=style,
        )])

    rows.append([InlineKeyboardButton(
        text=" بحث جديد",
        callback_data=f"sv_search:{supplier_id}",
        style="primary",
    )])
    rows.append([InlineKeyboardButton(
        text=" رجوع",
        callback_data=f"shop_supplier_{supplier_id}",
        style="primary",
    )])

    text = NL.join([
        f" <b>البحث عن: {query}</b>",
        "",
        f" تم إيجاد <b>{len(products)}</b> نتيجة:",
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(SupplierSearch.waiting_query)
async def supplier_search_wrong_type_handler(message: Message, state: FSMContext) -> None:
    """Handle non-text during search."""
    await message.answer(" الرجاء إرسال كلمة بحث نصية.")

