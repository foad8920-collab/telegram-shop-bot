"""Unified shop search  local subscriptions + suppliers."""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func as sa_func, or_ as sa_or

from bot.database.main import Database
from bot.database.models.main import (
    LocalSubscription, SupplierProduct, Supplier,
)
from bot.states import ShopStates


logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)

_MAX_PER_SOURCE = 20


async def _safe_answer(call: CallbackQuery):
    try:
        await call.answer()
    except TelegramBadRequest:
        pass


def _fmt(value) -> str:
    try:
        return f"{Decimal(str(value)).quantize(Decimal(chr(39) + chr(48) + chr(46) + chr(48) + chr(49) + chr(39)))}" + chr(36)
    except Exception:
        return f"{value}$"


def _local_kb(items: list) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for sub in items:
        qty = int(getattr(sub, "sellable_quantity", None) or sub.quantity or 0)
        available = qty > 0
        if available:
            text = f" {sub.name}  {_fmt(sub.price)} (متوفر: {qty})"
            cb = f"local_sub:{sub.id}"
            style = "success"
        else:
            text = f" {sub.name}  نفدت"
            cb = "local_sub_out_of_stock"
            style = "danger"
        rows.append([InlineKeyboardButton(text=text[:64], callback_data=cb, style=style)])
    return rows


def _supplier_kb(items: list, supplier_id: int) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for prod in items:
        stock = int(prod.stock or 0)
        available = prod.is_available and (prod.is_unlimited or stock > 0)
        if available:
            icon = ""
            suffix = ""
            style = "success"
        else:
            icon = ""
            suffix = " (نفدت)"
            style = "danger"
        name = (prod.name or "")[:40]
        price = _fmt(prod.selling_price) if prod.selling_price else "?"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {name}  {price}{suffix}"[:64],
            callback_data=f"sv_prod:{supplier_id}:{prod.id}",
            style=style,
        )])
    return rows


@router.callback_query(F.data == "shop_search")
async def shop_search_start_handler(call: CallbackQuery, state: FSMContext) -> None:
    """Start unified search."""
    await _safe_answer(call)

    text = NL.join([
        " <b>البحث في المنتجات</b>",
        "",
        " <b>أرسل كلمة للبحث عنها:</b>",
        "",
        " يشمل البحث: <b>الاشتراكات المحلية</b> + <b>موردي المتجر</b>",
        "",
        " للإلغاء: <code>/cancel</code>",
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=" رجوع", callback_data="shop", style="primary")],
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML", reply_markup=markup)

    await state.set_state(ShopStates.waiting_search_query)


@router.message(ShopStates.waiting_search_query, F.text)
async def shop_search_query_handler(message: Message, state: FSMContext) -> None:
    """Receive query, search across local + suppliers."""
    query = (message.text or "").strip()

    if query == "/cancel":
        await state.clear()
        await message.answer(" تم الإلغاء.")
        return

    if len(query) < 2:
        await message.answer(" الكلمة قصيرة جداً (الحد 2 أحرف).")
        return

    if len(query) > 50:
        await message.answer(" الكلمة طويلة جداً (الحد 50 حرف).")
        return

    await state.clear()

    query_lower = query.lower()

    # ---- Search local subscriptions ----
    async with Database().session() as session:
        local_result = await session.execute(
            select(LocalSubscription)
            .where(
                LocalSubscription.is_active == True,
                sa_func.lower(LocalSubscription.name).like(f"%{query_lower}%"),
            )
            .order_by(LocalSubscription.id)
            .limit(_MAX_PER_SOURCE)
        )
        local_items = list(local_result.scalars().all())

    # ---- Search supplier products ----
    async with Database().session() as session:
        supplier_result = await session.execute(
            select(SupplierProduct)
            .where(
                SupplierProduct.is_visible == True,
                sa_func.lower(SupplierProduct.name).like(f"%{query_lower}%"),
                SupplierProduct.supplier_id.in_([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
            )
            .order_by(
                SupplierProduct.supplier_id.asc(),
                SupplierProduct.is_available.desc(),
                SupplierProduct.selling_price.asc(),
            )
            .limit(_MAX_PER_SOURCE * 3)
        )
        supplier_items_all = list(supplier_result.scalars().all())

        # Get suppliers info
        suppliers_result = await session.execute(select(Supplier))
        suppliers_map = {s.id: s for s in suppliers_result.scalars().all()}

    # Group by supplier
    by_supplier: dict[int, list] = {}
    for prod in supplier_items_all:
        by_supplier.setdefault(prod.supplier_id, []).append(prod)

    total = len(local_items) + len(supplier_items_all)

    # ---- Empty result ----
    if total == 0:
        text = NL.join([
            f" <b>البحث عن: {query}</b>",
            "",
            " لا توجد نتائج.",
            " جرّب كلمة أخرى.",
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" بحث جديد", callback_data="shop_search", style="primary")],
            [InlineKeyboardButton(text=" رجوع", callback_data="shop", style="primary")],
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=markup)
        return

    # ---- Build results ----
    rows: list[list[InlineKeyboardButton]] = []

    if local_items:
        rows.append([InlineKeyboardButton(
            text=f" الاشتراكات المحلية ({len(local_items)})",
            callback_data="noop",
            style="primary",
        )])
        rows.extend(_local_kb(local_items))

    for sid, prods in by_supplier.items():
        sup = suppliers_map.get(sid)
        if sup is None:
            continue
        icon = sup.icon_emoji or ""
        name = sup.display_name or sup.name
        rows.append([InlineKeyboardButton(
            text=f"{icon} {name} ({len(prods)})",
            callback_data="noop",
            style="primary",
        )])
        rows.extend(_supplier_kb(prods, sid))

    rows.append([InlineKeyboardButton(text=" بحث جديد", callback_data="shop_search", style="primary")])
    rows.append([InlineKeyboardButton(text=" رجوع", callback_data="shop", style="primary")])

    text = NL.join([
        f" <b>البحث عن: {query}</b>",
        "",
        f" تم إيجاد <b>{total}</b> نتيجة:",
    ])

    try:
        await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest as e:
        logger.exception("Failed to send search results: %s", e)


@router.message(ShopStates.waiting_search_query)
async def shop_search_wrong_type_handler(message: Message, state: FSMContext) -> None:
    await message.answer(" الرجاء إرسال كلمة بحث نصية.")


@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery) -> None:
    await _safe_answer(call)

# ============================================================
#  Local subscriptions search (from local subscriptions menu)
# ============================================================

@router.callback_query(F.data == "local_search")
async def local_search_start_handler(call: CallbackQuery, state: FSMContext) -> None:
    """Start search in local subscriptions only."""
    await _safe_answer(call)

    text = NL.join([
        " <b>البحث في الاشتراكات المحلية</b>",
        "",
        " <b>أرسل كلمة للبحث عنها:</b>",
        "",
        "<i>مثال: Netflix, Gemini, VPN...</i>",
        "",
        " للإلغاء: <code>/cancel</code>",
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=" رجوع", callback_data="shop_local", style="primary")],
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML", reply_markup=markup)

    # Use a separate state to differentiate
    await state.set_state(ShopStates.waiting_search_query)
    await state.update_data(search_target="local_only")


@router.message(ShopStates.waiting_search_query, F.text)
async def shop_search_query_handler_dispatch(message: Message, state: FSMContext) -> None:
    """Dispatch based on search target."""
    data = await state.get_data()
    target = data.get("search_target", "all")

    query = (message.text or "").strip()

    if query == "/cancel":
        await state.clear()
        await message.answer(" تم الإلغاء.")
        return

    if len(query) < 2:
        await message.answer(" الكلمة قصيرة جداً (الحد 2 أحرف).")
        return

    if len(query) > 50:
        await message.answer(" الكلمة طويلة جداً (الحد 50 حرف).")
        return

    await state.clear()

    query_lower = query.lower()

    if target == "local_only":
        await _search_local_only(message, query, query_lower)
    else:
        # This shouldn't happen if dispatch order is right, but fallback
        await _search_all(message, query, query_lower)


async def _search_local_only(message: Message, query: str, query_lower: str) -> None:
    """Search only in local_subscriptions."""
    async with Database().session() as session:
        result = await session.execute(
            select(LocalSubscription)
            .where(
                LocalSubscription.is_active == True,
                sa_func.lower(LocalSubscription.name).like(f"%{query_lower}%"),
            )
            .order_by(LocalSubscription.id)
            .limit(30)
        )
        items = list(result.scalars().all())

    if not items:
        text = NL.join([
            f" <b>البحث عن: {query}</b>",
            "",
            " لا توجد نتائج في الاشتراكات المحلية.",
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" بحث جديد", callback_data="local_search", style="primary")],
            [InlineKeyboardButton(text=" رجوع", callback_data="shop_local", style="primary")],
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=markup)
        return

    rows = _local_kb(items)
    rows.append([InlineKeyboardButton(text=" بحث جديد", callback_data="local_search", style="primary")])
    rows.append([InlineKeyboardButton(text=" رجوع", callback_data="shop_local", style="primary")])

    text = NL.join([
        f" <b>البحث عن: {query}</b>",
        "",
        f" <b>الاشتراكات المحلية</b> ({len(items)} نتيجة):",
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _search_all(message: Message, query: str, query_lower: str) -> None:
    """Search all (local + suppliers). Called from shop_search."""
    # This is the same as the main handler
    # Copy of the existing shop_search_query_handler logic
    async with Database().session() as session:
        local_result = await session.execute(
            select(LocalSubscription)
            .where(
                LocalSubscription.is_active == True,
                sa_func.lower(LocalSubscription.name).like(f"%{query_lower}%"),
            )
            .order_by(LocalSubscription.id)
            .limit(_MAX_PER_SOURCE)
        )
        local_items = list(local_result.scalars().all())

        supplier_result = await session.execute(
            select(SupplierProduct)
            .where(
                SupplierProduct.is_visible == True,
                sa_func.lower(SupplierProduct.name).like(f"%{query_lower}%"),
            )
            .order_by(
                SupplierProduct.supplier_id.asc(),
                SupplierProduct.is_available.desc(),
                SupplierProduct.selling_price.asc(),
            )
            .limit(_MAX_PER_SOURCE * 3)
        )
        supplier_items_all = list(supplier_result.scalars().all())

        suppliers_result = await session.execute(select(Supplier))
        suppliers_map = {s.id: s for s in suppliers_result.scalars().all()}

    by_supplier: dict[int, list] = {}
    for prod in supplier_items_all:
        by_supplier.setdefault(prod.supplier_id, []).append(prod)

    total = len(local_items) + len(supplier_items_all)

    if total == 0:
        text = NL.join([
            f" <b>البحث عن: {query}</b>",
            "",
            " لا توجد نتائج.",
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" بحث جديد", callback_data="shop_search", style="primary")],
            [InlineKeyboardButton(text=" رجوع", callback_data="shop", style="primary")],
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=markup)
        return

    rows: list[list[InlineKeyboardButton]] = []

    if local_items:
        rows.append([InlineKeyboardButton(
            text=f" الاشتراكات المحلية ({len(local_items)})",
            callback_data="noop",
            style="primary",
        )])
        rows.extend(_local_kb(local_items))

    for sid, prods in by_supplier.items():
        sup = suppliers_map.get(sid)
        if sup is None:
            continue
        icon = sup.icon_emoji or ""
        name = sup.display_name or sup.name
        rows.append([InlineKeyboardButton(
            text=f"{icon} {name} ({len(prods)})",
            callback_data="noop",
            style="primary",
        )])
        rows.extend(_supplier_kb(prods, sid))

    rows.append([InlineKeyboardButton(text=" بحث جديد", callback_data="shop_search", style="primary")])
    rows.append([InlineKeyboardButton(text=" رجوع", callback_data="shop", style="primary")])

    text = NL.join([
        f" <b>البحث عن: {query}</b>",
        "",
        f" تم إيجاد <b>{total}</b> نتيجة:",
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

