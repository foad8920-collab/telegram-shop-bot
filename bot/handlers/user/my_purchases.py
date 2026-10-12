"""User purchases page  unified view across all sources."""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func as sa_func, desc

from bot.database.main import Database
from bot.database.models.main import (
    BoughtGoods, SupplierOrder, SupplierProduct, Supplier,
    LocalSubscriptionOrder, LocalSubscription,
)


logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)


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


def _truncate(text: str, length: int = 40) -> str:
    text = str(text or "-")
    return text[:length] + ("..." if len(text) > length else "")


# ============================================================
#  Main purchases page
# ============================================================

@router.callback_query(F.data == "bought_items")
async def my_purchases_handler(call: CallbackQuery, state: FSMContext) -> None:
    """Show purchases menu."""
    await _safe_answer(call)

    user_id = call.from_user.id

    # Count each source
    async with Database().session() as session:
        r1 = await session.execute(
            select(sa_func.count()).select_from(BoughtGoods).where(BoughtGoods.buyer_id == user_id)
        )
        gen_count = int(r1.scalar() or 0)

        r2 = await session.execute(
            select(sa_func.count()).select_from(SupplierOrder).where(SupplierOrder.user_id == user_id)
        )
        sup_count = int(r2.scalar() or 0)

        r3 = await session.execute(
            select(sa_func.count()).select_from(LocalSubscriptionOrder).where(
                LocalSubscriptionOrder.user_id == user_id
            )
        )
        loc_count = int(r3.scalar() or 0)

    text = NL.join([
        "<b>مشترياتي</b>",
        "====================",
        "",
        f"اختر المصدر:",
    ])

    rows: list[list[InlineKeyboardButton]] = []

    if sup_count > 0:
        rows.append([InlineKeyboardButton(
            text=f"من الموردين ({sup_count})",
            callback_data="my_purchases:suppliers:0",
            style="primary",
        )])

    if loc_count > 0:
        rows.append([InlineKeyboardButton(
            text=f"من الاشتراكات المحلية ({loc_count})",
            callback_data="my_purchases:local:0",
            style="success",
        )])

    if gen_count > 0:
        rows.append([InlineKeyboardButton(
            text=f"من المتجر العام ({gen_count})",
            callback_data="my_purchases:general:0",
            style="primary",
        )])

    if sup_count == 0 and loc_count == 0 and gen_count == 0:
        text = NL.join([
            "<b>مشترياتي</b>",
            "====================",
            "",
            "لا توجد مشتريات بعد.",
            "",
            "تصفح المتجر وابدأ الشراء.",
        ])

    rows.append([InlineKeyboardButton(text="رجوع", callback_data="profile", style="primary")])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ============================================================
#  Suppliers list
# ============================================================

_PER_PAGE = 10


@router.callback_query(F.data.startswith("my_purchases:suppliers:"))
async def my_purchases_suppliers(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)

    try:
        page = int(call.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0

    user_id = call.from_user.id

    async with Database().session() as session:
        count_r = await session.execute(
            select(sa_func.count()).select_from(SupplierOrder).where(SupplierOrder.user_id == user_id)
        )
        total = int(count_r.scalar() or 0)

        r = await session.execute(
            select(SupplierOrder)
            .where(SupplierOrder.user_id == user_id)
            .order_by(desc(SupplierOrder.id))
            .offset(page * _PER_PAGE)
            .limit(_PER_PAGE)
        )
        orders = list(r.scalars().all())

        # Fetch products
        product_ids = [o.product_id for o in orders if o.product_id]
        products_map = {}
        suppliers_map = {}
        if product_ids:
            pr = await session.execute(
                select(SupplierProduct).where(SupplierProduct.id.in_(product_ids))
            )
            products_map = {p.id: p for p in pr.scalars().all()}

            supplier_ids = list({p.supplier_id for p in products_map.values()})
            if supplier_ids:
                sr = await session.execute(
                    select(Supplier).where(Supplier.id.in_(supplier_ids))
                )
                suppliers_map = {s.id: s for s in sr.scalars().all()}

    if total == 0:
        text = "لا توجد مشتريات من الموردين."
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="رجوع", callback_data="bought_items", style="primary")],
        ])
        try:
            await call.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass
        return

    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        status_icon = {
            "completed": chr(0x2705),
            "pending": chr(0x23F3),
            "processing": chr(0x1F504),
            "failed": chr(0x274C),
        }.get(order.status or "", chr(0x2753))

        style = {
            "completed": "success",
            "pending": "primary",
            "processing": "primary",
            "failed": "danger",
        }.get(order.status or "", "primary")

        prod = products_map.get(order.product_id)
        prod_name = _truncate(prod.name if prod else f"#{order.product_id}", 35)
        price = _fmt(order.selling_price) if order.selling_price else "-"

        rows.append([InlineKeyboardButton(
            text=f"{status_icon} {prod_name} - {price}",
            callback_data=f"my_order:supplier:{order.id}",
            style=style,
        )])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="السابق", callback_data=f"my_purchases:suppliers:{page - 1}", style="primary"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop", style="primary"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="التالي", callback_data=f"my_purchases:suppliers:{page + 1}", style="primary"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="رجوع", callback_data="bought_items", style="primary")])

    text = NL.join([
        "<b>مشترياتي من الموردين</b>",
        "====================",
        f"الاجمالي: <b>{total}</b>",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        pass


# ============================================================
#  Local subscriptions list
# ============================================================

@router.callback_query(F.data.startswith("my_purchases:local:"))
async def my_purchases_local(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)

    try:
        page = int(call.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0

    user_id = call.from_user.id

    async with Database().session() as session:
        count_r = await session.execute(
            select(sa_func.count()).select_from(LocalSubscriptionOrder).where(
                LocalSubscriptionOrder.user_id == user_id
            )
        )
        total = int(count_r.scalar() or 0)

        r = await session.execute(
            select(LocalSubscriptionOrder)
            .where(LocalSubscriptionOrder.user_id == user_id)
            .order_by(desc(LocalSubscriptionOrder.id))
            .offset(page * _PER_PAGE)
            .limit(_PER_PAGE)
        )
        orders = list(r.scalars().all())

        sub_ids = [o.subscription_id for o in orders if o.subscription_id]
        subs_map = {}
        if sub_ids:
            sr = await session.execute(
                select(LocalSubscription).where(LocalSubscription.id.in_(sub_ids))
            )
            subs_map = {s.id: s for s in sr.scalars().all()}

    if total == 0:
        text = "لا توجد مشتريات من الاشتراكات المحلية."
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="رجوع", callback_data="bought_items", style="primary")],
        ])
        try:
            await call.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass
        return

    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        status_icon = {
            "completed": chr(0x2705),
            "pending": chr(0x23F3),
            "approved": chr(0x1F504),
            "rejected": chr(0x274C),
            "cancelled": chr(0x26D4),
        }.get(order.status or "", chr(0x2753))

        style = {
            "completed": "success",
            "pending": "primary",
            "approved": "primary",
            "rejected": "danger",
            "cancelled": "danger",
        }.get(order.status or "", "primary")

        sub = subs_map.get(order.subscription_id)
        sub_name = _truncate(sub.name if sub else f"#{order.subscription_id}", 35)
        price = _fmt(order.total_price) if order.total_price else "-"

        rows.append([InlineKeyboardButton(
            text=f"{status_icon} {sub_name} - {price}",
            callback_data=f"my_order:local:{order.id}",
            style=style,
        )])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="السابق", callback_data=f"my_purchases:local:{page - 1}", style="primary"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop", style="primary"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="التالي", callback_data=f"my_purchases:local:{page + 1}", style="primary"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="رجوع", callback_data="bought_items", style="primary")])

    text = NL.join([
        "<b>مشترياتي من الاشتراكات المحلية</b>",
        "====================",
        f"الاجمالي: <b>{total}</b>",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        pass


# ============================================================
#  General store list
# ============================================================

@router.callback_query(F.data.startswith("my_purchases:general:"))
async def my_purchases_general(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)

    try:
        page = int(call.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0

    user_id = call.from_user.id

    async with Database().session() as session:
        count_r = await session.execute(
            select(sa_func.count()).select_from(BoughtGoods).where(BoughtGoods.buyer_id == user_id)
        )
        total = int(count_r.scalar() or 0)

        r = await session.execute(
            select(BoughtGoods)
            .where(BoughtGoods.buyer_id == user_id)
            .order_by(desc(BoughtGoods.id))
            .offset(page * _PER_PAGE)
            .limit(_PER_PAGE)
        )
        items = list(r.scalars().all())

    if total == 0:
        text = "لا توجد مشتريات من المتجر العام."
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="رجوع", callback_data="bought_items", style="primary")],
        ])
        try:
            await call.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass
        return

    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        name = _truncate(item.item_name, 35)
        price = _fmt(item.price) if item.price else "-"
        rows.append([InlineKeyboardButton(
            text=f"{chr(0x2705)} {name} - {price}",
            callback_data=f"my_order:general:{item.id}",
            style="success",
        )])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="السابق", callback_data=f"my_purchases:general:{page - 1}", style="primary"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop", style="primary"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="التالي", callback_data=f"my_purchases:general:{page + 1}", style="primary"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="رجوع", callback_data="bought_items", style="primary")])

    text = NL.join([
        "<b>مشترياتي من المتجر العام</b>",
        "====================",
        f"الاجمالي: <b>{total}</b>",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        pass


# ============================================================
#  Single order details
# ============================================================

@router.callback_query(F.data.startswith("my_order:"))
async def my_order_details(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)

    try:
        parts = call.data.split(":")
        source = parts[1]
        order_id = int(parts[2])
    except (IndexError, ValueError):
        await call.answer("بيانات غير صالحة.", show_alert=True)
        return

    user_id = call.from_user.id

    if source == "supplier":
        text, back_cb = await _order_details_supplier(order_id, user_id)
    elif source == "local":
        text, back_cb = await _order_details_local(order_id, user_id)
    elif source == "general":
        text, back_cb = await _order_details_general(order_id, user_id)
    else:
        await call.answer("مصدر غير معروف.", show_alert=True)
        return

    if text is None:
        await call.answer("الطلب غير موجود.", show_alert=True)
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="رجوع", callback_data=back_cb, style="primary")],
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        pass


async def _order_details_supplier(order_id: int, user_id: int):
    async with Database().session() as session:
        order = await session.get(SupplierOrder, order_id)
        if order is None or order.user_id != user_id:
            return None, ""

        prod = await session.get(SupplierProduct, order.product_id)
        prod_name = prod.name if prod else f"#{order.product_id}"

    lines = [
        f"<b>{prod_name}</b>",
        "====================",
        f"السعر: <b>{_fmt(order.selling_price)}</b>",
        f"الكمية: <b>{order.quantity}</b>",
        f"الطلب: <code>#{order.id}</code>",
        "====================",
    ]

    if order.status == "completed":
        lines.append(f"{chr(0x2705)} <b>تم التسليم</b>")
        content = None
        if isinstance(order.response_data, dict):
            content = order.response_data.get("content")
        if content:
            lines.append("")
            lines.append("<b>المحتوى:</b>")
            lines.append(f"<code>{content}</code>")
    elif order.status == "failed":
        lines.append(f"{chr(0x274C)} <b>مرفوض</b>")
        if order.error_message:
            lines.append("")
            lines.append(f"<b>السبب:</b> {order.error_message}")
    else:
        lines.append(f"{chr(0x23F3)} <b>جاري المعالجة...</b>")

    return NL.join(lines), "my_purchases:suppliers:0"


async def _order_details_local(order_id: int, user_id: int):
    async with Database().session() as session:
        order = await session.get(LocalSubscriptionOrder, order_id)
        if order is None or order.user_id != user_id:
            return None, ""

        sub = await session.get(LocalSubscription, order.subscription_id)
        sub_name = sub.name if sub else f"#{order.subscription_id}"

    lines = [
        f"<b>{sub_name}</b>",
        "====================",
        f"السعر: <b>{_fmt(order.total_price)}</b>",
        f"الكمية: <b>{order.quantity}</b>",
        f"الطلب: <code>#{order.id}</code>",
        "====================",
    ]

    if order.status == "completed":
        lines.append(f"{chr(0x2705)} <b>تم التسليم</b>")
        if order.delivered_content:
            lines.append("")
            lines.append("<b>المحتوى:</b>")
            lines.append(f"<code>{order.delivered_content}</code>")
        elif order.admin_note:
            lines.append("")
            lines.append("<b>التعليمات:</b>")
            lines.append(order.admin_note)
    elif order.status in ("rejected", "cancelled"):
        lines.append(f"{chr(0x274C)} <b>مرفوض</b>")
        if order.admin_note:
            lines.append("")
            lines.append(f"<b>السبب:</b> {order.admin_note}")
    else:
        lines.append(f"{chr(0x23F3)} <b>جاري المعالجة...</b>")

    return NL.join(lines), "my_purchases:local:0"


async def _order_details_general(order_id: int, user_id: int):
    async with Database().session() as session:
        item = await session.get(BoughtGoods, order_id)
        if item is None or item.buyer_id != user_id:
            return None, ""

    lines = [
        f"<b>{item.item_name}</b>",
        "====================",
        f"السعر: <b>{_fmt(item.price)}</b>",
        f"الطلب: <code>#{item.id}</code>",
        "====================",
        f"{chr(0x2705)} <b>تم التسليم</b>",
    ]

    if item.value:
        lines.append("")
        lines.append("<b>المحتوى:</b>")
        lines.append(f"<code>{item.value}</code>")

    return NL.join(lines), "my_purchases:general:0"


@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery) -> None:
    await _safe_answer(call)
