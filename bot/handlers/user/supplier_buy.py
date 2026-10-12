"""Supplier product purchase flow."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import (
    SupplierProduct, Supplier, PaymentMethod,
    SupplierOrder, User,
)
from bot.states import SupplierBuy
from bot.misc import EnvKeys


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


async def _get_user_balance(user_id: int) -> Decimal:
    async with Database().session() as session:
        r = await session.execute(select(User.balance).where(User.telegram_id == user_id))
        row = r.first()
    if row is None:
        return Decimal("0.00")
    return Decimal(str(row[0] or 0)).quantize(Decimal("0.01"))


def _get_admin_ids() -> list[int]:
    result: list[int] = []
    admins_raw = getattr(EnvKeys, "ADMINS", None)
    if admins_raw:
        if isinstance(admins_raw, (list, tuple, set)):
            for x in admins_raw:
                try:
                    result.append(int(x))
                except (ValueError, TypeError):
                    pass
        else:
            for p in str(admins_raw).split(","):
                p = p.strip()
                if p:
                    try:
                        result.append(int(p))
                    except ValueError:
                        pass
    owner = getattr(EnvKeys, "OWNER_ID", None)
    if owner:
        try:
            result.append(int(owner))
        except (ValueError, TypeError):
            pass
    return list(dict.fromkeys(result))


# ============================================================
#  Quantity keyboard (smart presets)
# ============================================================

def _quantity_keyboard(product_id: int, max_qty: int) -> InlineKeyboardMarkup:
    """Smart quantity picker (presets + manual)."""
    rows: list[list[InlineKeyboardButton]] = []

    PRESETS = [1, 2, 3, 5, 10, 25, 50, 100]
    avail = [n for n in PRESETS if n <= max_qty]

    current_row: list[InlineKeyboardButton] = []
    for n in avail:
        current_row.append(InlineKeyboardButton(
            text=str(n),
            callback_data=f"sb_qty:{product_id}:{n}",
            style="primary",
        ))
        if len(current_row) == 5:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    if max_qty > 100:
        rows.append([InlineKeyboardButton(
            text=f"كل الكمية ({max_qty})",
            callback_data=f"sb_qty:{product_id}:{max_qty}",
            style="success",
        )])

    rows.append([InlineKeyboardButton(
        text="ادخال رقم مخصص",
        callback_data=f"sb_qty_manual:{product_id}",
        style="primary",
    )])
    rows.append([InlineKeyboardButton(
        text="رجوع",
        callback_data=f"sv_prod:0:{product_id}",
        style="primary",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _payment_keyboard(product_id: int, qty: int, total: Decimal,
                      balance: Decimal, methods: list) -> InlineKeyboardMarkup:
    """Payment method picker."""
    rows: list[list[InlineKeyboardButton]] = []

    if balance >= total:
        rows.append([InlineKeyboardButton(
            text="رصيد البوت (فوري)",
            callback_data=f"sb_pay_balance:{product_id}:{qty}",
            style="success",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="رصيد البوت (غير كاف)",
            callback_data="sb_insufficient",
            style="danger",
        )])

    for pm in methods:
        style = (pm.style or "primary").lower()
        if style not in {"success", "primary", "danger"}:
            style = "primary"
        rows.append([InlineKeyboardButton(
            text=pm.name,
            callback_data=f"sb_pay:{product_id}:{qty}:{pm.id}",
            style=style,
        )])

    rows.append([InlineKeyboardButton(
        text="رجوع",
        callback_data=f"sv_prod:0:{product_id}",
        style="primary",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
#  Step 1: Enter quantity
# ============================================================

@router.callback_query(F.data.startswith("sv_buy:"))
async def supplier_buy_start(call: CallbackQuery, state: FSMContext) -> None:
    """Enter quantity picker."""
    await _safe_answer(call)

    try:
        _, sid_s, pid_s = call.data.split(":")
        supplier_id = int(sid_s)
        product_id = int(pid_s)
    except (ValueError, AttributeError):
        return

    async with Database().session() as session:
        product = await session.get(SupplierProduct, product_id)
        if product is None or product.supplier_id != supplier_id:
            await call.answer("المنتج غير موجود.", show_alert=True)
            return

        max_qty = int(product.stock or 0)
        if product.is_unlimited:
            max_qty = 999

        if max_qty <= 0:
            await call.answer("نفدت الكمية.", show_alert=True)
            return

        price = Decimal(str(product.selling_price or 0))
        name = str(product.name or "منتج")

    await state.update_data(
        sb_supplier_id=supplier_id,
        sb_product_id=product_id,
        sb_product_name=name,
        sb_unit_price=str(price),
        sb_max_qty=max_qty,
    )
    await state.set_state(SupplierBuy.choosing_quantity)

    text = NL.join([
        f"<b>{name}</b>",
        "====================",
        f"السعر للوحدة: <b>{_fmt(price)}</b>",
        f"المتوفر: <b>{max_qty}</b>",
        "",
        "<b>اختر الكمية المطلوبة:</b>",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=_quantity_keyboard(product_id, max_qty))
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML",
                                  reply_markup=_quantity_keyboard(product_id, max_qty))


@router.callback_query(F.data.startswith("sb_qty:"), SupplierBuy.choosing_quantity)
async def supplier_buy_qty_selected(call: CallbackQuery, state: FSMContext) -> None:
    """Process quantity selection."""
    await _safe_answer(call)

    try:
        _, pid_s, qty_s = call.data.split(":")
        product_id = int(pid_s)
        qty = int(qty_s)
    except (ValueError, AttributeError):
        return

    data = await state.get_data()
    if data.get("sb_product_id") != product_id:
        await call.answer("انتهت الجلسة.", show_alert=True)
        return

    max_qty = int(data.get("sb_max_qty", 0))
    if qty < 1 or qty > max_qty:
        await call.answer("الكمية غير صالحة.", show_alert=True)
        return

    unit_price = Decimal(str(data.get("sb_unit_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))

    await state.update_data(sb_qty=qty, sb_total=str(total))

    balance = await _get_user_balance(call.from_user.id)

    async with Database().session() as session:
        r = await session.execute(
            select(PaymentMethod).where(PaymentMethod.is_active == True).order_by(PaymentMethod.id)  # noqa: E712
        )
        methods = list(r.scalars().all())

    await state.set_state(SupplierBuy.choosing_payment)

    product_name = data.get("sb_product_name", "-")
    insufficient = balance < total
    warning = (NL + "رصيدك غير كاف - اختر طريقة دفع اخرى.") if insufficient else ""

    text = NL.join([
        "<b>اختر طريقة الدفع</b>",
        "====================",
        f"المنتج: <b>{product_name}</b>",
        f"الكمية: <b>{qty}</b>",
        f"الاجمالي: <b>{_fmt(total)}</b>",
        f"رصيدك: <b>{_fmt(balance)}</b>" + warning,
        "====================",
        "<b>الطرق المتاحة:</b>",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML",
            reply_markup=_payment_keyboard(product_id, qty, total, balance, methods))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("sb_qty_manual:"))
async def supplier_buy_qty_manual(call: CallbackQuery, state: FSMContext) -> None:
    """Ask user to type custom quantity."""
    await _safe_answer(call)

    try:
        product_id = int(call.data.split(":")[1])
    except (ValueError, AttributeError):
        return

    data = await state.get_data()
    if data.get("sb_product_id") != product_id:
        await call.answer("انتهت الجلسة.", show_alert=True)
        return

    max_qty = int(data.get("sb_max_qty", 0))

    await state.set_state(SupplierBuy.waiting_manual_quantity)

    text = NL.join([
        "<b>ادخال كمية مخصصة</b>",
        "====================",
        f"الحد الاقصى: <b>{max_qty}</b>",
        "",
        "ارسل الرقم المطلوب:",
        "",
        "للالغاء: /cancel",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="رجوع", callback_data=f"sv_prod:0:{product_id}", style="primary"),
            ]]))
    except TelegramBadRequest:
        pass


@router.message(SupplierBuy.waiting_manual_quantity, F.text)
async def supplier_buy_qty_manual_receive(message: Message, state: FSMContext) -> None:
    """Process custom quantity."""
    raw = (message.text or "").strip()

    if raw == "/cancel":
        await state.clear()
        await message.answer("تم الالغاء.")
        return

    try:
        qty = int(raw)
    except ValueError:
        await message.answer("الرجاء ادخال رقم صحيح.")
        return

    data = await state.get_data()
    product_id = data.get("sb_product_id")
    max_qty = int(data.get("sb_max_qty", 0))

    if not product_id:
        await state.clear()
        return

    if qty < 1:
        await message.answer("الكمية يجب ان تكون 1 على الاقل.")
        return

    if qty > max_qty:
        await message.answer(f"الكمية المطلوبة غير متوفرة. الحد الاقصى: <b>{max_qty}</b>", parse_mode="HTML")
        return

    unit_price = Decimal(str(data.get("sb_unit_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))

    await state.update_data(sb_qty=qty, sb_total=str(total))
    await state.set_state(SupplierBuy.choosing_payment)

    balance = await _get_user_balance(message.from_user.id)

    async with Database().session() as session:
        r = await session.execute(
            select(PaymentMethod).where(PaymentMethod.is_active == True).order_by(PaymentMethod.id)  # noqa: E712
        )
        methods = list(r.scalars().all())

    product_name = data.get("sb_product_name", "-")
    insufficient = balance < total
    warning = (NL + "رصيدك غير كاف.") if insufficient else ""

    text = NL.join([
        "<b>اختر طريقة الدفع</b>",
        "====================",
        f"المنتج: <b>{product_name}</b>",
        f"الكمية: <b>{qty}</b>",
        f"الاجمالي: <b>{_fmt(total)}</b>",
        f"رصيدك: <b>{_fmt(balance)}</b>" + warning,
        "====================",
        "<b>الطرق المتاحة:</b>",
    ])

    await message.answer(text, parse_mode="HTML",
        reply_markup=_payment_keyboard(product_id, qty, total, balance, methods))


@router.callback_query(F.data == "sb_insufficient")
async def supplier_buy_insufficient(call: CallbackQuery) -> None:
    await call.answer("رصيدك غير كاف.", show_alert=True)


# ============================================================
#  Step 3: Balance payment (instant)
# ============================================================

@router.callback_query(F.data.startswith("sb_pay_balance:"), SupplierBuy.choosing_payment)
async def supplier_buy_balance(call: CallbackQuery, state: FSMContext) -> None:
    """Pay instantly from user balance."""
    await _safe_answer(call)

    try:
        _, pid_s, qty_s = call.data.split(":")
        product_id = int(pid_s)
        qty = int(qty_s)
    except (ValueError, AttributeError):
        return

    data = await state.get_data()
    if data.get("sb_product_id") != product_id or data.get("sb_qty") != qty:
        await call.answer("انتهت الجلسة.", show_alert=True)
        return

    unit_price = Decimal(str(data.get("sb_unit_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))
    user_id = call.from_user.id

    try:
        async with Database().session() as session:
            # Lock user
            user = (await session.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )).scalar_one_or_none()
            if user is None:
                await call.answer("الحساب غير موجود.", show_alert=True)
                return

            user_balance = Decimal(str(user.balance or 0)).quantize(Decimal("0.01"))
            if user_balance < total:
                await call.answer(f"رصيدك غير كاف.", show_alert=True)
                return

            # Lock product
            product = (await session.execute(
                select(SupplierProduct).where(SupplierProduct.id == product_id).with_for_update()
            )).scalar_one_or_none()
            if product is None:
                await call.answer("المنتج غير موجود.", show_alert=True)
                return

            if not product.is_unlimited and int(product.stock or 0) < qty:
                await call.answer("الكمية لم تعد متوفرة.", show_alert=True)
                return

            # Deduct balance
            user.balance = (user_balance - total).quantize(Decimal("0.01"))

            # Deduct stock
            if not product.is_unlimited:
                product.stock = max(0, int(product.stock or 0) - qty)

            # Create order
            order = SupplierOrder(
                supplier_id=product.supplier_id,
                user_id=user_id,
                product_id=product_id,
                quantity=qty,
                cost_price=product.cost_price,
                selling_price=unit_price,
                profit=(unit_price - Decimal(str(product.cost_price or 0))) * qty,
                status="pending",
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

            order_id = order.id
            product_name = str(product.name)
            supplier_id = product.supplier_id
            new_balance = user.balance

    except Exception as e:
        logger.exception("Supplier balance purchase failed: %s", e)
        await call.answer("حدث خطا. لم يتم الخصم.", show_alert=True)
        return

    await state.clear()

    # Fetch supplier info for admin notification
    async with Database().session() as session:
        supplier = await session.get(Supplier, supplier_id)
        supplier_name = supplier.display_name or supplier.name if supplier else "-"
        supplier_icon = supplier.icon_emoji if supplier else ""
        supplier_url = supplier.api_url if supplier else ""

    # Notify admins
    bot: Bot = call.bot
    admin_text = NL.join([
        f"{supplier_icon} <b>طلب شراء جديد من {supplier_name}</b>",
        "====================",
        f"رقم الطلب: <code>#{order_id}</code>",
        f"العميل: <code>{user_id}</code>",
        f"المنتج: <b>{product_name}</b>",
        f"الكمية: <b>{qty}</b>",
        f"الاجمالي: <b>{_fmt(total)}</b>",
        f"طريقة الدفع: <b>رصيد البوت</b>",
        "====================",
        "<b>يجب شراء المنتج من لوحة المورد</b>",
    ])

    admin_kb_rows: list[list[InlineKeyboardButton]] = []
    if supplier_url and supplier_url.startswith("http"):
        admin_kb_rows.append([InlineKeyboardButton(text=f"فتح {supplier_name}", url=supplier_url, style="primary")])
    admin_kb_rows.append([
        InlineKeyboardButton(
            text=chr(0x1F4E4) + " إرسال الاشتراك للعميل",
            callback_data=f"sb_send_content:{order_id}",
            style="success",
        ),
    ])
    admin_kb_rows.append([
        InlineKeyboardButton(
            text=chr(0x274C) + " رفض الطلب",
            callback_data=f"sb_reject:{order_id}",
            style="danger",
        ),
    ])

    admin_markup = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)

    for admin_id in _get_admin_ids():
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=admin_markup)
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)

    # Notify user
    user_text = NL.join([
        "تم إتمام طلبك بنجاح!",
        "====================",
        f"المنتج: <b>{product_name}</b>",
        f"الكمية: <b>{qty}</b>",
        f"الاجمالي: <b>{_fmt(total)}</b>",
        f"رقم الطلب: <code>#{order_id}</code>",
        f"رصيدك الجديد: <b>{_fmt(new_balance)}</b>",
        "====================",
        "سيتم ارسال تفاصيل المنتج اليك قريبا.",
    ])

    try:
        await call.message.edit_text(user_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="رجوع للمتجر", callback_data="shop", style="primary"),
            ]]))
    except TelegramBadRequest:
        await call.message.answer(user_text, parse_mode="HTML")


# ============================================================
#  Step 4: Wallet payment (pending + notify admin)
# ============================================================

@router.callback_query(F.data.startswith("sb_pay:"), SupplierBuy.choosing_payment)
async def supplier_buy_wallet(call: CallbackQuery, state: FSMContext) -> None:
    """Show wallet info + ask for receipt photo."""
    await _safe_answer(call)

    try:
        parts = call.data.split(":")
        product_id = int(parts[1])
        qty = int(parts[2])
        method_id = int(parts[3])
    except (ValueError, AttributeError, IndexError):
        return

    data = await state.get_data()
    if data.get("sb_product_id") != product_id or data.get("sb_qty") != qty:
        await call.answer("انتهت الجلسة.", show_alert=True)
        return

    async with Database().session() as session:
        pm = await session.get(PaymentMethod, method_id)
        if pm is None or not pm.is_active:
            await call.answer("طريقة الدفع غير متاحة.", show_alert=True)
            return

    unit_price = Decimal(str(data.get("sb_unit_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))

    await state.update_data(sb_payment_method_id=pm.id, sb_payment_method_name=pm.name)
    await state.set_state(SupplierBuy.waiting_wallet_proof)

    text = NL.join([
        f"<b>{pm.name}</b>",
        "====================",
        f"المبلغ المطلوب: <b>{_fmt(total)}</b>",
        "",
        "<b>بيانات الحساب:</b>",
        f"<code>{pm.account_info}</code>",
        "",
        "====================",
        "1) حوّل المبلغ للحساب اعلاه.",
        "2) ارسل <b>صورة سند الحوالة</b> هنا.",
        "",
        "سيتم التحقق بواسطة الادارة.",
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="الغاء", callback_data=f"sv_prod:0:{product_id}", style="danger"),
            ]]))
    except TelegramBadRequest:
        pass
