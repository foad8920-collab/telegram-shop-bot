"""Local subscription purchase flow.

Steps:
    1. start_buy_flow()        user clicked "Buy Now" on a subscription
    2. choose_quantity()       render quantity picker
    3. quantity_selected()     store quantity, go to payment
    4. choose_payment()        render payment method picker
    5. payment_selected()      dispatch to specific payment handler
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func
from sqlalchemy.orm import noload

from bot.database.main import Database
from bot.database.models.main import (
    LocalSubscription, PaymentMethod, User,
    LocalSubscriptionOrder, LocalSubscriptionCode,
)
from bot.states import LocalBuy


logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)


async def _safe_answer(call: CallbackQuery):
    try:
        await call.answer()
    except TelegramBadRequest:
        pass


async def _get_user_balance(user_id: int) -> Decimal:
    """Return current balance of the given telegram user (0 if missing)."""
    async with Database().session() as session:
        result = await session.execute(
            select(User.balance).where(User.telegram_id == user_id)
        )
        row = result.first()
    if row is None:
        return Decimal("0.00")
    return Decimal(str(row[0] or 0)).quantize(Decimal("0.01"))


def _fmt(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}" + chr(36)


def _quantity_keyboard(sub_id: int, max_qty: int) -> InlineKeyboardMarkup:
    """Smart quantity picker with preset values + manual input."""
    rows: list[list[InlineKeyboardButton]] = []

    # Preset values (only those <= max_qty)
    PRESETS = [1, 2, 3, 5, 10, 25, 50, 100]
    available_presets = [n for n in PRESETS if n <= max_qty]

    # Build preset grid (up to 5 per row)
    current_row: list[InlineKeyboardButton] = []
    for n in available_presets:
        current_row.append(InlineKeyboardButton(
            text=str(n),
            callback_data=f"lb_qty:{sub_id}:{n}",
            style="primary",
        ))
        if len(current_row) == 5:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    # "All quantity" button if max > 100
    if max_qty > 100:
        rows.append([InlineKeyboardButton(
            text=f" كل الكمية ({max_qty})",
            callback_data=f"lb_qty:{sub_id}:{max_qty}",
            style="success",
        )])

    # Manual input button
    rows.append([InlineKeyboardButton(
        text=" إدخال رقم مخصص",
        callback_data=f"lb_qty_manual:{sub_id}",
        style="primary",
    )])

    # Back
    rows.append([InlineKeyboardButton(
        text=" رجوع",
        callback_data=f"local_sub:{sub_id}",
        style="primary",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _payment_keyboard(sub_id: int, qty: int, total: Decimal,
                      balance: Decimal, methods: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if balance >= total:
        balance_text = " رصيد البوت (فوري)"
        balance_cb = f"lb_pay_balance:{sub_id}:{qty}"
        balance_style = "success"
    else:
        balance_text = " رصيد البوت (غير كافٍ)"
        balance_cb = "lb_insufficient_balance"
        balance_style = "danger"
    rows.append([InlineKeyboardButton(
        text=balance_text,
        callback_data=balance_cb,
        style=balance_style,
    )])
    for pm in methods:
        style = (pm.style or "primary").lower()
        if style not in {"success", "primary", "danger"}:
            style = "primary"
        rows.append([InlineKeyboardButton(
            text=pm.name,
            callback_data=f"lb_pay:{sub_id}:{qty}:{pm.id}",
            style=style,
        )])
    rows.append([InlineKeyboardButton(
        text=" رجوع",
        callback_data=f"local_buy:{sub_id}",
        style="primary",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def start_buy_flow(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    try:
        sub_id = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer("معرّف الاشتراك غير صالح.", show_alert=True)
        return
    async with Database().session() as session:
        sub = await session.get(LocalSubscription, sub_id)
        if sub is None or not sub.is_active:
            await call.answer("هذا الاشتراك لم يعد متوفرًا.", show_alert=True)
            return
        delivery_type = (sub.delivery_type or "codes").lower()

        if delivery_type == "codes":
            cpp = int(sub.codes_per_purchase or 1)
            if cpp < 1:
                cpp = 1
            free_count = (await session.execute(
                select(func.count()).select_from(LocalSubscriptionCode).where(
                    LocalSubscriptionCode.subscription_id == sub_id,
                    LocalSubscriptionCode.is_used == False,  # noqa: E712
                )
            )).scalar() or 0
            available_qty = int(free_count) // cpp
        else:
            available_qty = int(sub.quantity or 0)

        if available_qty <= 0:
            await call.answer(" نفدت الكمية من هذا الاشتراك.", show_alert=True)
            return
    balance = await _get_user_balance(call.from_user.id)
    await state.update_data(
        sub_id=sub_id,
        sub_name=str(sub.name),
        sub_price=str(sub.price),
        sub_delivery=sub.delivery_type or "codes",
        sub_max_qty=available_qty,
    )
    await state.set_state(LocalBuy.choosing_quantity)
    text = NL.join([
        f" <b>{sub.name}</b>",
        "",
        f" السعر للوحدة: <b>{sub.price}$</b>",
        f" المتوفر: <b>{available_qty}</b>",
        f" رصيدك: <b>{_fmt(balance)}</b>",
        "",
        "<b>اختر الكمية المطلوبة:</b>",
    ])
    await call.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_quantity_keyboard(sub_id, available_qty),
    )


@router.callback_query(F.data.startswith("lb_qty:"), LocalBuy.choosing_quantity)
async def quantity_selected(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    try:
        _, sub_id_s, qty_s = call.data.split(":")
        sub_id = int(sub_id_s)
        qty = int(qty_s)
    except (ValueError, AttributeError):
        await call.answer("بيانات غير صالحة.", show_alert=True)
        return
    data = await state.get_data()
    if data.get("sub_id") != sub_id:
        await call.answer("انتهت الجلسة. أعد المحاولة.", show_alert=True)
        return
    max_qty = int(data.get("sub_max_qty", 0))
    if qty < 1 or qty > max_qty:
        await call.answer("الكمية غير صالحة.", show_alert=True)
        return
    unit_price = Decimal(str(data.get("sub_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))
    await state.update_data(qty=qty, total_price=str(total))
    balance = await _get_user_balance(call.from_user.id)
    async with Database().session() as session:
        result = await session.execute(
            select(PaymentMethod).where(PaymentMethod.is_active == True).order_by(PaymentMethod.id)
        )
        methods = result.scalars().all()
    await state.set_state(LocalBuy.choosing_payment)
    sub_name = data.get("sub_name", "")
    insufficient = balance < total
    warning = (NL + " <b>رصيدك غير كافٍ  اختر طريقة دفع أخرى.</b>") if insufficient else ""
    text = NL.join([
        " <b>اختر طريقة الدفع</b>",
        "",
        f" المنتج: <b>{sub_name}</b>",
        f" الكمية: <b>{qty}</b>",
        f" الإجمالي: <b>{_fmt(total)}</b>",
        f" رصيدك: <b>{_fmt(balance)}</b>" + warning,
        "",
        "<b>الطرق المتاحة:</b>",
    ])
    await call.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_payment_keyboard(sub_id, qty, total, balance, methods),
    )


@router.callback_query(F.data == "lb_insufficient_balance", LocalBuy.choosing_payment)
async def insufficient_balance_handler(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    total = Decimal(str(data.get("total_price", "0"))).quantize(Decimal("0.01"))
    balance = await _get_user_balance(call.from_user.id)
    await call.answer(
        f" رصيدك {_fmt(balance)} أقل من المطلوب {_fmt(total)}.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("lb_pay_balance:"), LocalBuy.choosing_payment)
async def payment_balance_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _safe_answer(call)
    try:
        _, sub_id_s, qty_s = call.data.split(":")
        sub_id = int(sub_id_s)
        qty = int(qty_s)
    except (ValueError, AttributeError):
        await call.answer("بيانات غير صالحة.", show_alert=True)
        return
    data = await state.get_data()
    if data.get("sub_id") != sub_id or data.get("qty") != qty:
        await call.answer("انتهت الجلسة. أعد المحاولة.", show_alert=True)
        return
    unit_price = Decimal(str(data.get("sub_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))
    user_id = call.from_user.id
    try:
        async with Database().session() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )).scalar_one_or_none()
            if user is None:
                await call.answer("تعذّر العثور على حسابك.", show_alert=True)
                return
            user_balance = Decimal(str(user.balance or 0)).quantize(Decimal("0.01"))
            if user_balance < total:
                await call.answer(
                    f" رصيدك {_fmt(user_balance)} أقل من المطلوب {_fmt(total)}.",
                    show_alert=True,
                )
                return
            sub = (await session.execute(
                select(LocalSubscription).where(LocalSubscription.id == sub_id).with_for_update()
            )).scalar_one_or_none()
            if sub is None or not sub.is_active:
                await call.answer("هذا الاشتراك لم يعد متوفرًا.", show_alert=True)
                return
            codes_per_purchase = int(sub.codes_per_purchase or 1)
            if codes_per_purchase < 1:
                codes_per_purchase = 1

            delivery_type = (sub.delivery_type or "codes").lower()

            if delivery_type == "codes":
                required_codes = qty * codes_per_purchase
                free_count = (await session.execute(
                    select(func.count()).select_from(LocalSubscriptionCode).where(
                        LocalSubscriptionCode.subscription_id == sub_id,
                        LocalSubscriptionCode.is_used == False,  # noqa: E712
                    )
                )).scalar() or 0
                if int(free_count) < required_codes:
                    await call.answer(
                        f" الأكواد المتاحة لا تكفي (المطلوب {required_codes}).",
                        show_alert=True,
                    )
                    return
            else:
                if int(sub.quantity or 0) < qty:
                    await call.answer(" الكمية المطلوبة لم تعد متوفرة.", show_alert=True)
                    return
                required_codes = 0
            codes = []
            if required_codes > 0:
                result = await session.execute(
                    select(LocalSubscriptionCode)
                    .where(
                        LocalSubscriptionCode.subscription_id == sub_id,
                        LocalSubscriptionCode.is_used == False,
                    )
                    .options(noload(LocalSubscriptionCode.subscription))
                    .order_by(LocalSubscriptionCode.id)
                    .limit(required_codes)
                    .with_for_update()
                )
                codes = list(result.scalars().all())
                if len(codes) < required_codes:
                    await call.answer(
                        f" الأكواد المتاحة لا تكفي (المطلوب {required_codes}).",
                        show_alert=True,
                    )
                    return
            user.balance = (user_balance - total).quantize(Decimal("0.01"))
            if delivery_type != "codes":
                sub.quantity = int(sub.quantity or 0) - qty
            order = LocalSubscriptionOrder(
                user_id=user_id,
                subscription_id=sub_id,
                quantity=qty,
                unit_price=unit_price,
                total_price=total,
                payment_method_id=None,
                payment_type="balance",
                status="completed",
                decided_at=datetime.now(timezone.utc),
            )
            session.add(order)
            await session.flush()
            for code in codes:
                code.is_used = True
                code.used_by = user_id
                code.order_id = order.id
                code.used_at = datetime.now(timezone.utc)
            if codes:
                content = NL.join(f" {c.content}" for c in codes)
                order.delivered_content = content
            await session.commit()
            order_id = order.id
            codes_content = [c.content for c in codes]
            new_balance = user.balance
            sub_name = str(sub.name)
    except Exception as e:
        logger.exception("Balance purchase failed: %s", e)
        await call.answer(
            " حدث خطأ غير متوقع. لم يتم الخصم. حاول لاحقًا.",
            show_alert=True,
        )
        return
    await state.clear()
    lines = [
        " <b>تم إتمام الشراء بنجاح!</b>",
        "",
        f" المنتج: <b>{sub_name}</b>",
        f" الكمية: <b>{qty}</b>",
        f" الإجمالي: <b>{_fmt(total)}</b>",
        f" رقم الطلب: <code>#{order_id}</code>",
        f" رصيدك الجديد: <b>{_fmt(new_balance)}</b>",
        "",
    ]
    # ---- نحدد السلوك حسب نوع التسليم ----
    delivery_type_after = (sub_delivery or "codes").lower() if 'sub_delivery' in dir() else "codes"

    # a) codes: تسليم فوري (كما هو)
    if codes_content:
        lines.append(" <b>الأكواد / المفاتيح الخاصة بك:</b>")
        lines.append("")
        for idx, c in enumerate(codes_content, 1):
            lines.append(f"<b>{idx}.</b> <code>{c}</code>")
        lines.append("")
        lines.append(" <i>احتفظ بهذه الأكواد  لن تُعرض مرة أخرى.</i>")
        text = NL.join(lines)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
        ])
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest:
            await call.message.answer(text, parse_mode="HTML", reply_markup=markup)
    else:
        # b) invite: نحن بحاجة لبيانات العميل
        # c) manual: نحن بحاجة لتواصل يدوي
        # نقرأ الاشتراك لمعرفة نوع الحقل
        invite_field = "email"
        try:
            async with Database().session() as _sess3:
                _sub3 = (await _sess3.execute(
                    select(LocalSubscription).where(LocalSubscription.id == sub_id)
                )).scalar_one_or_none()
                if _sub3 is not None:
                    invite_field = (_sub3.invite_field_type or "email").lower()
        except Exception as e:
            logger.warning("Failed to reload sub in balance: %s", e)

        if delivery_type_after == "invite" and invite_field != "none":
            # نطلب من العميل إرسال البريد + نضع awaiting_data في DB (fallback)
            try:
                async with Database().session() as _sess4:
                    _o4 = (await _sess4.execute(
                        select(LocalSubscriptionOrder)
                        .options(noload(LocalSubscriptionOrder.subscription))
                        .where(LocalSubscriptionOrder.id == order_id)
                    )).scalar_one_or_none()
                    if _o4 is not None:
                        _o4.awaiting_data = True
                        _o4.awaiting_field_type = invite_field
                        await _sess4.commit()
            except Exception as e:
                logger.warning("Failed to set awaiting_data: %s", e)

            field_titles = {
                "email": " البريد الإلكتروني",
                "phone": " رقم الجوال",
                "username": " اسم المستخدم",
                "telegram_id": " معرّف تيليجرام",
                "text": " النص المطلوب",
            }
            field_label = field_titles.get(invite_field, " البيانات المطلوبة")

            prompt_text = NL.join([
                " <b>تم إتمام الشراء بنجاح!</b>",
                "",
                f" المنتج: <b>{sub_name}</b>",
                f" الكمية: <b>{qty}</b>",
                f" الإجمالي: <b>{_fmt(total)}</b>",
                f" رقم الطلب: <code>#{order_id}</code>",
                "",
                f"{field_label}:",
                "<i>مثال: user@gmail.com</i>",
                "",
                " <i>جاري تفعيل اشتراكك من قبل المشرف  يرجى الانتظار.</i>",
            ])
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
            ])
            try:
                await call.message.edit_text(prompt_text, parse_mode="HTML", reply_markup=markup)
            except TelegramBadRequest:
                await call.message.answer(prompt_text, parse_mode="HTML", reply_markup=markup)

            # نضع FSM للمشتري  للاستقبال الفوري
            try:
                from aiogram.fsm.storage.base import StorageKey
                from bot.states import LocalBuy as _LB
                # نحتاج dispatcher  نأخذه من call (لا يوجد)، لذا نعتمد على DB-fallback
                # سيتولى customer_data_handler  لكننا نحتاج state
                # الطريقة الأنظف: نضع حالة FSM عبر storage إن أمكن
                # بما أننا في handler، لا dispatcher متاح  نستخدم DB فقط
                # والـ handler العام سيتعامل
            except Exception as e:
                logger.warning("FSM not set for balance invite: %s", e)
        else:
            # manual
            manual_text = NL.join([
                " <b>تم إتمام الشراء بنجاح!</b>",
                "",
                f" المنتج: <b>{sub_name}</b>",
                f" الكمية: <b>{qty}</b>",
                f" الإجمالي: <b>{_fmt(total)}</b>",
                f" رقم الطلب: <code>#{order_id}</code>",
                "",
                " <i>جاري تفعيل اشتراكك من قبل المشرف  يرجى الانتظار.</i>",
            ])
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
            ])
            try:
                await call.message.edit_text(manual_text, parse_mode="HTML", reply_markup=markup)
            except TelegramBadRequest:
                await call.message.answer(manual_text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith("lb_pay:"), LocalBuy.choosing_payment)
async def payment_method_handler(call: CallbackQuery, state: FSMContext) -> None:
    """User selected a payment method (not balance)."""
    await _safe_answer(call)

    # Format: lb_pay:{sub_id}:{qty}:{method_id}
    try:
        parts = call.data.split(":")
        if len(parts) != 4:
            raise ValueError("bad parts count")
        _, sub_id_s, qty_s, method_id_s = parts
        sub_id = int(sub_id_s)
        qty = int(qty_s)
        method_id = int(method_id_s)
    except (ValueError, AttributeError, IndexError):
        await call.answer("بيانات غير صالحة.", show_alert=True)
        return

    data = await state.get_data()
    if data.get("sub_id") != sub_id or data.get("qty") != qty:
        await call.answer("انتهت الجلسة. أعد المحاولة.", show_alert=True)
        return

    # Fetch payment method
    async with Database().session() as session:
        pm = await session.get(PaymentMethod, method_id)
        if pm is None or not pm.is_active:
            await call.answer("طريقة الدفع هذه لم تعد متاحة.", show_alert=True)
            return

    method_name_lower = (pm.name or "").lower()
    is_binance = ("بايننس" in pm.name) or ("binance" in method_name_lower) or (pm.id == 1)

    await state.update_data(payment_method_id=pm.id, payment_method_name=pm.name)

    total = Decimal(str(data.get("total_price", "0"))).quantize(Decimal("0.01"))

    if is_binance:
        # الطريقة الآلية: نطلب كود/رقم عملية بايننس
        await state.set_state(LocalBuy.waiting_binance_code)

        text = NL.join([
            " <b>الدفع عبر Binance</b>",
            "",
            f" المبلغ المطلوب: <b>{_fmt(total)}</b>",
            "",
            " <b>بيانات الحساب:</b>",
            f"<code>{pm.account_info}</code>",
            "",
            "",
            " حوّل المبلغ إلى الحساب أعلاه.",
            " ثم انسخ <b>رقم عملية التحويل (Reference/Order ID)</b> والصقه هنا.",
            "",
            " <i>أرسل الرقم فقط بدون أي نص إضافي.</i>",
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" إلغاء", callback_data=f"local_sub:{sub_id}", style="danger")],
        ])
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        return

    # الطريقة اليدوية (محافظ)
    await state.set_state(LocalBuy.waiting_wallet_proof)

    text = NL.join([
        f" <b>الدفع عبر {pm.name}</b>",
        "",
        f" المبلغ المطلوب: <b>{_fmt(total)}</b>",
        "",
        " <b>بيانات الحساب:</b>",
        f"<code>{pm.account_info}</code>",
        "",
        "",
        " حوّل المبلغ إلى الحساب أعلاه.",
        " ثم أرسل <b>صورة سند الحوالة</b> هنا.",
        "",
        " <i>سيتم التحقق من الحوالة بواسطة الإدارة.</i>",
    ])
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=" إلغاء", callback_data=f"local_sub:{sub_id}", style="danger")],
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)

# ============================================================
#  Manual quantity input
# ============================================================

@router.callback_query(F.data.startswith("lb_qty_manual:"))
async def quantity_manual_start(call: CallbackQuery, state: FSMContext) -> None:
    """Ask user to type quantity."""
    await _safe_answer(call)

    try:
        sub_id = int(call.data.split(":")[1])
    except (IndexError, ValueError, AttributeError):
        await call.answer("بيانات غير صالحة.", show_alert=True)
        return

    data = await state.get_data()
    if data.get("sub_id") != sub_id:
        await call.answer("انتهت الجلسة. اعد المحاولة.", show_alert=True)
        return

    max_qty = int(data.get("sub_max_qty", 0))
    if max_qty <= 0:
        await call.answer("لا توجد كمية متاحة.", show_alert=True)
        return

    await state.update_data(manual_qty_sub_id=sub_id)
    await state.set_state(LocalBuy.waiting_manual_quantity)

    text = NL.join([
        " <b>إدخال كمية مخصصة</b>",
        "",
        f" الحد الأقصى المتاح: <b>{max_qty}</b>",
        "",
        " <b>أرسل الرقم المطلوب:</b>",
        "",
        f"<i>مثال: 7 (رقم بين 1 و {max_qty})</i>",
        "",
        " للإلغاء: <code>/cancel</code>",
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=" رجوع",
            callback_data=f"local_buy:{sub_id}",
            style="primary",
        )],
    ])

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(LocalBuy.waiting_manual_quantity, F.text)
async def quantity_manual_receive(message: Message, state: FSMContext) -> None:
    """Validate and process manual quantity."""
    raw = (message.text or "").strip()

    if raw == "/cancel":
        await state.clear()
        await message.answer("تم الإلغاء.")
        return

    # Parse number
    try:
        qty = int(raw)
    except ValueError:
        await message.answer("الرجاء ادخال رقم صحيح (بلا حروف).")
        return

    data = await state.get_data()
    sub_id = data.get("manual_qty_sub_id") or data.get("sub_id")
    max_qty = int(data.get("sub_max_qty", 0))

    if not sub_id:
        await message.answer("انتهت الجلسة.")
        await state.clear()
        return

    if qty < 1:
        await message.answer("الكمية يجب ان تكون 1 على الاقل.")
        return

    if qty > max_qty:
        await message.answer(
            f"الكمية المطلوبة غير متوفرة. الحد الاقصى: <b>{max_qty}</b>",
            parse_mode="HTML",
        )
        return

    # Valid  compute total
    unit_price = Decimal(str(data.get("sub_price", "0")))
    total = (unit_price * qty).quantize(Decimal("0.01"))

    await state.update_data(qty=qty, total_price=str(total))
    await state.set_state(LocalBuy.choosing_payment)

    # Fetch payment methods
    async with Database().session() as session:
        result = await session.execute(
            select(PaymentMethod)
            .where(PaymentMethod.is_active == True)  # noqa: E712
            .order_by(PaymentMethod.id)
        )
        methods = result.scalars().all()

    balance = await _get_user_balance(message.from_user.id)
    sub_name = data.get("sub_name", "-")

    insufficient = balance < total
    warning = (NL + " <b>رصيدك غير كافٍ - اختر طريقة دفع أخرى.</b>") if insufficient else ""

    text = NL.join([
        " <b>اختر طريقة الدفع</b>",
        "",
        f" المنتج: <b>{sub_name}</b>",
        f" الكمية: <b>{qty}</b>",
        f" الإجمالي: <b>{_fmt(total)}</b>",
        f" رصيدك: <b>{_fmt(balance)}</b>" + warning,
        "",
        "<b>الطرق المتاحة:</b>",
    ])

    try:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=_payment_keyboard(sub_id, qty, total, balance, methods),
        )
    except TelegramBadRequest:
        pass
