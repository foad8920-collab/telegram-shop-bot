"""Local subscription  receipt & binance code receivers."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import (
    LocalSubscription, LocalSubscriptionOrder, LocalSubscriptionCode,
    PaymentMethod, User,
)
from bot.states import LocalBuy
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
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}" + chr(36)


def _get_admin_ids() -> list[int]:
    """Return a list of admin telegram ids.

    Priority:
      1. EnvKeys.ADMINS (comma-separated)  if present
      2. EnvKeys.ADMIN_ID (single)  if present
      3. EnvKeys.OWNER_ID (single, guaranteed)
    """
    result: list[int] = []

    admins_raw = getattr(EnvKeys, "ADMINS", None)
    if admins_raw:
        if isinstance(admins_raw, (list, tuple, set)):
            for x in admins_raw:
                try:
                    result.append(int(x))
                except (ValueError, TypeError):
                    continue
        else:
            for p in str(admins_raw).split(","):
                p = p.strip()
                if not p:
                    continue
                try:
                    result.append(int(p))
                except ValueError:
                    continue

    single_raw = getattr(EnvKeys, "ADMIN_ID", None)
    if single_raw:
        try:
            result.append(int(single_raw))
        except (ValueError, TypeError):
            pass

    owner_raw = getattr(EnvKeys, "OWNER_ID", None)
    if owner_raw:
        try:
            owner = int(owner_raw)
            if owner not in result:
                result.append(owner)
        except (ValueError, TypeError):
            pass

    # unique
    return list(dict.fromkeys(result))


def _admin_review_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=" تأكيد",
                callback_data=f"local_order_approve:{order_id}",
                style="success",
            ),
            InlineKeyboardButton(
                text=" رفض",
                callback_data=f"local_order_reject:{order_id}",
                style="danger",
            ),
        ],
    ])


def _order_summary_text(
    order_id: int,
    user_id: int,
    username: str,
    sub_name: str,
    qty: int,
    total: Decimal,
    payment_name: str,
    reference: str | None = None,
) -> str:
    lines = [
        " <b>طلب شراء جديد</b>",
        "",
        f" الطلب: <code>#{order_id}</code>",
        f" المستخدم: <a href=\"tg://user?id={user_id}\">{username}</a>",
        f" <code>{user_id}</code>",
        f" المنتج: <b>{sub_name}</b>",
        f" الكمية: <b>{qty}</b>",
        f" الإجمالي: <b>{_fmt(total)}</b>",
        f" الدفع: <b>{payment_name}</b>",
    ]
    if reference:
        lines.append(f" المرجع: <code>{reference}</code>")
    lines.append("")
    return NL.join(lines)


# ============================================================
#  1) Wallet receipt  photo handler
# ============================================================

@router.message(LocalBuy.waiting_wallet_proof, F.photo)
async def wallet_proof_handler(message: Message, state: FSMContext) -> None:
    """User sent a receipt photo  create pending order and notify admins."""
    data = await state.get_data()
    sub_id = data.get("sub_id")
    qty = data.get("qty")
    payment_method_id = data.get("payment_method_id")

    if not sub_id or not qty or not payment_method_id:
        await message.answer(" انتهت الجلسة. أعد العملية من البداية.")
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    user_id = message.from_user.id
    username = message.from_user.full_name or message.from_user.username or f"User{user_id}"

    try:
        async with Database().session() as session:
            sub = (await session.execute(
                select(LocalSubscription).where(LocalSubscription.id == sub_id)
            )).scalar_one_or_none()
            pm = (await session.execute(
                select(PaymentMethod).where(PaymentMethod.id == payment_method_id)
            )).scalar_one_or_none()

            if sub is None or pm is None:
                await message.answer(" بيانات غير صحيحة.")
                await state.clear()
                return

            unit_price = Decimal(str(sub.price or 0)).quantize(Decimal("0.01"))
            total = (unit_price * int(qty)).quantize(Decimal("0.01"))

            order = LocalSubscriptionOrder(
                user_id=user_id,
                subscription_id=sub.id,
                quantity=int(qty),
                unit_price=unit_price,
                total_price=total,
                payment_method_id=pm.id,
                payment_type="wallet",
                status="pending",
                payment_proof_file_id=file_id,
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

            order_id = order.id
            sub_name = str(sub.name)
            pm_name = str(pm.name)

    except Exception as e:
        logger.exception("Failed to create wallet order: %s", e)
        await message.answer(" حدث خطأ أثناء إنشاء الطلب. حاول لاحقاً.")
        await state.clear()
        return

    # Notify admins
    summary = _order_summary_text(
        order_id=order_id,
        user_id=user_id,
        username=username,
        sub_name=sub_name,
        qty=int(qty),
        total=total,
        payment_name=pm_name,
    )
    markup = _admin_review_keyboard(order_id)

    bot: Bot = message.bot
    admins = _get_admin_ids()
    sent = 0
    for admin_id in admins:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=summary,
                parse_mode="HTML",
                reply_markup=markup,
            )
            sent += 1
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)

    # Confirm to user
    await message.answer(
        NL.join([
            " <b>تم إرسال طلبك بنجاح!</b>",
            "",
            f" رقم الطلب: <code>#{order_id}</code>",
            f" المبلغ: <b>{_fmt(total)}</b>",
            "",
            " <i>سيتم مراجعة الحوالة والتأكيد خلال دقائق.</i>",
            " <i>ستصلك رسالة عند الموافقة.</i>",
        ]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
        ]),
    )
    await state.clear()


@router.message(LocalBuy.waiting_wallet_proof)
async def wallet_proof_wrong_type(message: Message, state: FSMContext) -> None:
    """User sent text instead of photo."""
    await message.answer(" الرجاء إرسال <b>صورة سند الحوالة</b> (وليس نصاً).", parse_mode="HTML")


# ============================================================
#  2) Binance reference code handler
# ============================================================

@router.message(LocalBuy.waiting_binance_code, F.text)
async def binance_code_handler(message: Message, state: FSMContext) -> None:
    """User sent binance order reference  create pending order and notify admins."""
    reference = (message.text or "").strip()
    if not reference or len(reference) < 4 or len(reference) > 100:
        await message.answer(" رقم العملية غير صالح. أرسل رقماً بين 4 و 100 حرف.")
        return

    data = await state.get_data()
    sub_id = data.get("sub_id")
    qty = data.get("qty")
    payment_method_id = data.get("payment_method_id")

    if not sub_id or not qty or not payment_method_id:
        await message.answer(" انتهت الجلسة. أعد العملية من البداية.")
        await state.clear()
        return

    user_id = message.from_user.id
    username = message.from_user.full_name or message.from_user.username or f"User{user_id}"

    try:
        async with Database().session() as session:
            sub = (await session.execute(
                select(LocalSubscription).where(LocalSubscription.id == sub_id)
            )).scalar_one_or_none()
            pm = (await session.execute(
                select(PaymentMethod).where(PaymentMethod.id == payment_method_id)
            )).scalar_one_or_none()

            if sub is None or pm is None:
                await message.answer(" بيانات غير صحيحة.")
                await state.clear()
                return

            unit_price = Decimal(str(sub.price or 0)).quantize(Decimal("0.01"))
            total = (unit_price * int(qty)).quantize(Decimal("0.01"))

            order = LocalSubscriptionOrder(
                user_id=user_id,
                subscription_id=sub.id,
                quantity=int(qty),
                unit_price=unit_price,
                total_price=total,
                payment_method_id=pm.id,
                payment_type="binance",
                status="pending",
                payment_reference=reference,
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

            order_id = order.id
            sub_name = str(sub.name)
            pm_name = str(pm.name)

    except Exception as e:
        logger.exception("Failed to create binance order: %s", e)
        await message.answer(" حدث خطأ أثناء إنشاء الطلب. حاول لاحقاً.")
        await state.clear()
        return

    # Notify admins
    summary = _order_summary_text(
        order_id=order_id,
        user_id=user_id,
        username=username,
        sub_name=sub_name,
        qty=int(qty),
        total=total,
        payment_name=pm_name,
        reference=reference,
    )
    markup = _admin_review_keyboard(order_id)

    bot: Bot = message.bot
    admins = _get_admin_ids()
    for admin_id in admins:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=summary,
                parse_mode="HTML",
                reply_markup=markup,
            )
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)

    # Confirm to user
    await message.answer(
        NL.join([
            " <b>تم إرسال طلبك بنجاح!</b>",
            "",
            f" رقم الطلب: <code>#{order_id}</code>",
            f" المرجع: <code>{reference}</code>",
            f" المبلغ: <b>{_fmt(total)}</b>",
            "",
            " <i>سيتم مراجعة العملية خلال دقائق.</i>",
        ]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
        ]),
    )
    await state.clear()


# ============================================================
#  3) Customer data handler (for invite subscriptions)
# ============================================================

import re as _re


def _validate_customer_data(field_type: str, text: str) -> tuple[bool, str]:
    """Validate input based on field type. Returns (ok, error_msg)."""
    text = (text or "").strip()
    if not text:
        return False, " الرجاء إرسال بيانات صحيحة."

    ft = (field_type or "email").lower()

    if ft == "email":
        pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        if not _re.match(pattern, text):
            return False, " البريد الإلكتروني غير صالح. مثال: user@gmail.com"
        return True, ""

    if ft == "phone":
        # يبدأ بـ + أو رقم، 7-15 رقم
        cleaned = _re.sub(r"[\s\-()]", "", text)
        pattern = r"^\+?\d{7,15}$"
        if not _re.match(pattern, cleaned):
            return False, " رقم الجوال غير صالح. مثال: +967771234567"
        return True, ""

    if ft == "username":
        if not (text.startswith("@") or len(text) >= 3):
            return False, " اسم المستخدم غير صالح. مثال: @username"
        return True, ""

    if ft == "telegram_id":
        if not text.isdigit():
            return False, " معرّف تيليجرام يجب أن يكون رقماً."
        return True, ""

    if ft == "text":
        if len(text) > 500:
            return False, " النص طويل جداً (الحد 500 حرف)."
        return True, ""

    return True, ""


@router.message(LocalBuy.waiting_customer_data)
async def customer_data_handler(message: Message, state: FSMContext) -> None:
    """Receive customer data (email/phone/etc.) for an invite order."""
    data = await state.get_data()
    order_id = data.get("awaiting_order_id")
    field_type = (data.get("awaiting_field_type") or "email").lower()

    if not order_id:
        await message.answer(" لم نجد طلباً بانتظار البيانات. ابدأ من جديد.")
        await state.clear()
        return

    raw_text = (message.text or "").strip()
    ok, err = _validate_customer_data(field_type, raw_text)
    if not ok:
        await message.answer(err)
        return

    user_id = message.from_user.id

    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(LocalSubscriptionOrder).where(LocalSubscriptionOrder.id == order_id)
            )).scalar_one_or_none()

            if order is None:
                await message.answer(" الطلب غير موجود.")
                await state.clear()
                return

            if order.user_id != user_id:
                await message.answer(" لا يمكنك تعديل هذا الطلب.")
                await state.clear()
                return

            order.customer_data = raw_text
            await session.commit()

            # اجلب اسم الاشتراك
            sub = (await session.execute(
                select(LocalSubscription).where(LocalSubscription.id == order.subscription_id)
            )).scalar_one_or_none()
            sub_name = str(sub.name) if sub else ""

            buyer_id = order.user_id
            total_str = _fmt(order.total_price)
            order_qty = order.quantity

    except Exception as e:
        logger.exception("Failed to save customer_data: %s", e)
        await message.answer(" حدث خطأ. حاول مرة أخرى.")
        return

    # ---- إشعار المشرف ----
    bot: Bot = message.bot
    admins = _get_admin_ids()

    field_titles = {
        "email": " البريد",
        "phone": " الجوال",
        "username": " اسم المستخدم",
        "telegram_id": " معرّف تيليجرام",
        "text": " نص",
    }
    field_label = field_titles.get(field_type, " بيانات")

    admin_text = NL.join([
        f" <b>وصلت بيانات العميل  الطلب #{order_id}</b>",
        "",
        f" المنتج: <b>{sub_name}</b>",
        f" الكمية: <b>{order_qty}</b>",
        f" الإجمالي: <b>{total_str}</b>",
        f" المشتري: <code>{buyer_id}</code>",
        "",
        f"{field_label}: <code>{raw_text}</code>",
        "",
        " <i>يمكنك الآن تنفيذ التسليم يدوياً.</i>",
    ])

    admin_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=" إرسال التعليمات للعميل",
            callback_data=f"admin_order_instr:{order_id}",
            style="success",
        )],
    ])

    for admin_id in admins:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=admin_markup,
            )
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)

    # ---- تأكيد للمشتري ----
    await message.answer(
        NL.join([
            " <b>تم استلام بياناتك بنجاح!</b>",
            "",
            f" الطلب: <code>#{order_id}</code>",
            "",
            " <i>جاري تفعيل اشتراكك من قبل المشرف  يرجى الانتظار.</i>",
        ]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
        ]),
    )

    await state.clear()

