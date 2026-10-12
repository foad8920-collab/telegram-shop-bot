"""Supplier order  admin handlers (send content + reject)."""
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
    SupplierOrder, SupplierProduct, Supplier, User,
)
from bot.states import SupplierBuy
from bot.misc import EnvKeys


logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)


def _fmt(value) -> str:
    try:
        return f"{Decimal(str(value)).quantize(Decimal(chr(39) + chr(48) + chr(46) + chr(48) + chr(49) + chr(39)))}" + chr(36)
    except Exception:
        return f"{value}$"


async def _safe_answer(call: CallbackQuery):
    try:
        await call.answer()
    except TelegramBadRequest:
        pass


def _admin_ids() -> list[int]:
    result = []
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


def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


async def _replace_caption_or_text(call: CallbackQuery, new_caption: str, new_markup=None):
    try:
        await call.message.edit_caption(caption=new_caption, parse_mode="HTML", reply_markup=new_markup)
        return
    except TelegramBadRequest:
        pass
    try:
        await call.message.edit_text(new_caption, parse_mode="HTML", reply_markup=new_markup)
    except TelegramBadRequest:
        pass


# ============================================================
#  SEND CONTENT (deliver subscription)
# ============================================================

@router.callback_query(F.data.startswith("sb_send_content:"))
async def supplier_send_content_start(call: CallbackQuery, state: FSMContext) -> None:
    """Admin clicked "send subscription"  enter FSM to type content."""
    await _safe_answer(call)

    if not _is_admin(call.from_user.id):
        await call.answer("ليس لديك صلاحية.", show_alert=True)
        return

    try:
        order_id = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer("معرّف غير صالح.", show_alert=True)
        return

    async with Database().session() as session:
        order = await session.get(SupplierOrder, order_id)
        if order is None:
            await call.answer("الطلب غير موجود.", show_alert=True)
            return

        if order.status not in ("pending", "processing"):
            await call.answer(f"الطلب في حالة: {order.status}.", show_alert=True)
            return

        buyer_id = order.user_id
        quantity = order.quantity

        product = await session.get(SupplierProduct, order.product_id)
        product_name = str(product.name) if product else ""

    await state.update_data(admin_order_id=order_id, admin_buyer_id=buyer_id)
    await state.set_state(SupplierBuy.waiting_delivery_content)

    text = NL.join([
        f"{chr(0x1F4E4)} <b>إرسال الاشتراك - الطلب #{order_id}</b>",
        "====================",
        f"العميل: <code>{buyer_id}</code>",
        f"المنتج: <b>{product_name}</b>",
        f"الكمية: <b>{quantity}</b>",
        "",
        f"<b>{chr(0x270D)} الصق الكود / الرابط / التعليمات:</b>",
        "",
        "للالغاء: <code>/cancel</code>",
    ])

    try:
        await call.message.answer(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.message(SupplierBuy.waiting_delivery_content, F.text)
async def supplier_send_content_receive(message: Message, state: FSMContext) -> None:
    """Admin sent the content  deliver to buyer."""
    if not _is_admin(message.from_user.id):
        await message.answer("ليس لديك صلاحية.")
        await state.clear()
        return

    content = (message.text or "").strip()

    if content == "/cancel":
        await state.clear()
        await message.answer("تم الالغاء.")
        return

    if len(content) < 2:
        await message.answer("النص قصير جدا.")
        return

    if len(content) > 4000:
        await message.answer("النص طويل جدا (الحد 4000 حرف).")
        return

    data = await state.get_data()
    order_id = data.get("admin_order_id")
    buyer_id = data.get("admin_buyer_id")

    if not order_id or not buyer_id:
        await message.answer("انتهت الجلسة.")
        await state.clear()
        return

    admin_id = message.from_user.id

    # Save to DB
    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(SupplierOrder).where(SupplierOrder.id == order_id).with_for_update()
            )).scalar_one_or_none()

            if order is None:
                await message.answer("الطلب غير موجود.")
                await state.clear()
                return

            if order.status not in ("pending", "processing"):
                await message.answer(f"الطلب في حالة: {order.status}.")
                await state.clear()
                return

            order.status = "completed"
            order.completed_at = datetime.now(timezone.utc)
            order.response_data = {"content": content, "delivered_by": admin_id}

            await session.commit()

            quantity = order.quantity
            total = _fmt(order.selling_price) if order.selling_price else "-"

            product = await session.get(SupplierProduct, order.product_id)
            product_name = str(product.name) if product else "-"

    except Exception as e:
        logger.exception("Failed to save delivery: %s", e)
        await message.answer("حدث خطأ. حاول مجددا.")
        return

    # Send to buyer
    bot: Bot = message.bot
    buyer_text = NL.join([
        f"{chr(0x1F381)} <b>تم تسليم طلبك بنجاح!</b>",
        "====================",
        f"المنتج: <b>{product_name}</b>",
        f"الكمية: <b>{quantity}</b>",
        f"رقم الطلب: <code>#{order_id}</code>",
        "====================",
        f"<b>{chr(0x1F4CB)} التفاصيل:</b>",
        "",
        content,
        "",
        "====================",
        f"{chr(0x1F4A1)} احتفظ بهذه الرسالة.",
    ])

    try:
        await bot.send_message(
            chat_id=buyer_id,
            text=buyer_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="رجوع للمتجر", callback_data="shop", style="primary"),
            ]]),
        )
    except Exception as e:
        logger.warning("Failed to send content to buyer %s: %s", buyer_id, e)
        await message.answer(f"تم الحفظ لكن فشل الارسال للعميل: {e}")
        await state.clear()
        return

    # Update admin
    await message.answer(
        NL.join([
            f"{chr(0x2705)} <b>تم التسليم - الطلب #{order_id}</b>",
            "====================",
            f"العميل: <code>{buyer_id}</code>",
            f"المنتج: <b>{product_name}</b>",
            f"الكمية: <b>{quantity}</b>",
            "====================",
            f"{chr(0x1F4E8)} تم ارسال المحتوى للعميل",
        ]),
        parse_mode="HTML",
    )

    await state.clear()


# ============================================================
#  REJECT (with reason + auto refund)
# ============================================================

@router.callback_query(F.data.startswith("sb_reject:"))
async def supplier_reject_start(call: CallbackQuery, state: FSMContext) -> None:
    """Admin clicked reject  ask for reason."""
    await _safe_answer(call)

    if not _is_admin(call.from_user.id):
        await call.answer("ليس لديك صلاحية.", show_alert=True)
        return

    try:
        order_id = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer("معرّف غير صالح.", show_alert=True)
        return

    async with Database().session() as session:
        order = await session.get(SupplierOrder, order_id)
        if order is None:
            await call.answer("الطلب غير موجود.", show_alert=True)
            return

        if order.status not in ("pending", "processing"):
            await call.answer(f"الطلب في حالة: {order.status}.", show_alert=True)
            return

        buyer_id = order.user_id

    await state.update_data(reject_order_id=order_id, reject_buyer_id=buyer_id)
    await state.set_state(SupplierBuy.waiting_reject_reason)

    text = NL.join([
        f"{chr(0x274C)} <b>رفض الطلب #{order_id}</b>",
        "====================",
        f"العميل: <code>{buyer_id}</code>",
        "",
        "<b>اكتب سبب الرفض:</b>",
        "(سيتم ارساله للعميل وارجاع رصيده)",
        "",
        "للالغاء: <code>/cancel</code>",
    ])

    try:
        await call.message.answer(text, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.message(SupplierBuy.waiting_reject_reason, F.text)
async def supplier_reject_reason_receive(message: Message, state: FSMContext) -> None:
    """Admin sent the reject reason  notify buyer + refund."""
    if not _is_admin(message.from_user.id):
        await message.answer("ليس لديك صلاحية.")
        await state.clear()
        return

    reason = (message.text or "").strip()

    if reason == "/cancel":
        await state.clear()
        await message.answer("تم الالغاء.")
        return

    if len(reason) < 2:
        await message.answer("اكتب سبب اوضح.")
        return

    data = await state.get_data()
    order_id = data.get("reject_order_id")
    buyer_id = data.get("reject_buyer_id")

    if not order_id or not buyer_id:
        await message.answer("انتهت الجلسة.")
        await state.clear()
        return

    refund_amount = Decimal("0")
    product_name = "-"

    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(SupplierOrder).where(SupplierOrder.id == order_id).with_for_update()
            )).scalar_one_or_none()

            if order is None:
                await message.answer("الطلب غير موجود.")
                await state.clear()
                return

            if order.status not in ("pending", "processing"):
                await message.answer(f"الطلب في حالة: {order.status}.")
                await state.clear()
                return

            # Refund only if paid via balance
            if order.selling_price and order.quantity:
                refund_amount = (Decimal(str(order.selling_price)) * order.quantity).quantize(Decimal("0.01"))

                user = (await session.execute(
                    select(User).where(User.telegram_id == buyer_id).with_for_update()
                )).scalar_one_or_none()

                if user is not None:
                    current = Decimal(str(user.balance or 0))
                    user.balance = (current + refund_amount).quantize(Decimal("0.01"))

                # Restore stock
                product = (await session.execute(
                    select(SupplierProduct).where(SupplierProduct.id == order.product_id).with_for_update()
                )).scalar_one_or_none()

                if product is not None and not product.is_unlimited:
                    product.stock = int(product.stock or 0) + int(order.quantity or 1)
                    product_name = str(product.name)

            order.status = "failed"
            order.completed_at = datetime.now(timezone.utc)
            order.error_message = reason[:500]

            await session.commit()

    except Exception as e:
        logger.exception("Failed to reject order: %s", e)
        await message.answer("حدث خطأ. حاول مجددا.")
        return

    # Send to buyer
    bot: Bot = message.bot
    buyer_text = NL.join([
        f"{chr(0x274C)} <b>ناسف، لم يتم تسليم طلبك.</b>",
        "====================",
        f"المنتج: <b>{product_name}</b>",
        f"رقم الطلب: <code>#{order_id}</code>",
        "====================",
        f"<b>السبب:</b>",
        "",
        reason,
        "",
        "====================",
    ])

    if refund_amount > 0:
        buyer_text += NL + f"{chr(0x1F4B0)} تم ارجاع <b>{_fmt(refund_amount)}</b> الى رصيدك."

    try:
        await bot.send_message(
            chat_id=buyer_id,
            text=buyer_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="رجوع للمتجر", callback_data="shop", style="primary"),
            ]]),
        )
    except Exception as e:
        logger.warning("Failed to notify buyer: %s", e)

    # Update admin
    await message.answer(
        NL.join([
            f"{chr(0x2705)} <b>تم رفض الطلب #{order_id}</b>",
            f"تم ارسال الاشعار للعميل وارجاع رصيده: <b>{_fmt(refund_amount)}</b>",
        ]),
        parse_mode="HTML",
    )

    await state.clear()
