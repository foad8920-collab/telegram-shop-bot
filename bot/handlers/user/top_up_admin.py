"""أزرار المشرف على طلبات الشحن  تأكيد/تعديل/رفض."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import TopUpRequest, User
from bot.handlers.user.balance_and_payment import _get_admin_ids_topup as _get_admin_ids
from bot.database.methods.read import invalidate_user_cache


def _fmt(value) -> str:
    from decimal import Decimal
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}" + chr(36)

logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)


class TopUpAdminStates(StatesGroup):
    entering_new_amount = State()


@router.callback_query(F.data.startswith("topup_approve:"))
async def approve(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user.id not in _get_admin_ids():
        return
    req_id = int(call.data.split(":")[1])

    async with Database().session() as session:
        req = (await session.execute(select(TopUpRequest).where(TopUpRequest.id == req_id))).scalar_one_or_none()
        if not req or req.status != "pending":
            await call.answer(" الطلب غير متاح.", show_alert=True)
            return

        user = (await session.execute(select(User).where(User.telegram_id == req.user_id))).scalar_one_or_none()
        if user:
            user.balance = (user.balance or Decimal(0)) + req.amount_requested
            req.amount_received = req.amount_requested
            req.status = "approved"
            req.admin_id = call.from_user.id
            req.reviewed_at = datetime.now(timezone.utc)
            await session.commit()
            await invalidate_user_cache(req.user_id)

    # تحديث رسالة المشرف
    try:
        await call.message.edit_caption(
            caption=(call.message.caption or "") + NL + NL + f" <b>تمت الموافقة على {_fmt(req.amount_requested)}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # إشعار المستخدم
    try:
        await call.bot.send_message(
            chat_id=req.user_id,
            text=NL.join([
                " <b>تم شحن رصيدك بنجاح!</b>",
                "",
                f" المبلغ: <b>{_fmt(req.amount_requested)}</b>",
                f" رقم الطلب: <code>#{req_id}</code>",
                "",
                " شكرًا لاستخدامك منصة اليمن الإلكترونية.",
            ]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" عرض الملف الشخصي", callback_data="profile", style="primary")]
            ]),
        )
    except Exception as e:
        logger.warning("User notify failed: %s", e)


@router.callback_query(F.data.startswith("topup_reject:"))
async def reject(call: CallbackQuery) -> None:
    await call.answer()
    if call.from_user.id not in _get_admin_ids():
        return
    req_id = int(call.data.split(":")[1])

    async with Database().session() as session:
        req = (await session.execute(select(TopUpRequest).where(TopUpRequest.id == req_id))).scalar_one_or_none()
        if not req or req.status != "pending":
            await call.answer(" الطلب غير متاح.", show_alert=True)
            return
        req.status = "rejected"
        req.admin_id = call.from_user.id
        req.reviewed_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        await call.message.edit_caption(
            caption=(call.message.caption or "") + NL + NL + " <b>تم رفض الطلب.</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    try:
        await call.bot.send_message(
            chat_id=req.user_id,
            text=NL.join([
                " <b>تم رفض طلب الشحن.</b>",
                "",
                f" رقم الطلب: <code>#{req_id}</code>",
                " للاستفسار، تواصل مع الدعم.",
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("User notify failed: %s", e)


@router.callback_query(F.data.startswith("topup_edit:"))
async def edit_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if call.from_user.id not in _get_admin_ids():
        return
    req_id = int(call.data.split(":")[1])
    await state.update_data(edit_req_id=req_id)
    await state.set_state(TopUpAdminStates.entering_new_amount)
    await call.message.answer(
        f" أرسل <b>المبلغ الفعلي</b> الذي وصل (رقم فقط):",
        parse_mode="HTML",
    )


@router.message(TopUpAdminStates.entering_new_amount, F.text)
async def edit_confirm(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in _get_admin_ids():
        return
    text = (message.text or "").strip().replace(",", ".")
    try:
        new_amount = Decimal(text)
        if new_amount <= 0:
            raise ValueError
    except Exception:
        await message.answer(" رقم غير صالح.")
        return

    data = await state.get_data()
    req_id = data.get("edit_req_id")
    await state.clear()

    async with Database().session() as session:
        req = (await session.execute(select(TopUpRequest).where(TopUpRequest.id == req_id))).scalar_one_or_none()
        if not req or req.status != "pending":
            await message.answer(" الطلب غير متاح.")
            return

        user = (await session.execute(select(User).where(User.telegram_id == req.user_id))).scalar_one_or_none()
        if user:
            user.balance = (user.balance or Decimal(0)) + new_amount
            req.amount_received = new_amount
            req.status = "approved"
            req.admin_id = message.from_user.id
            req.reviewed_at = datetime.now(timezone.utc)
            await session.commit()
            await invalidate_user_cache(req.user_id)

    await message.answer(f" <b>تمت الموافقة على {_fmt(new_amount)} وإضافتها للرصيد.</b>", parse_mode="HTML")

    try:
        await message.bot.send_message(
            chat_id=req.user_id,
            text=NL.join([
                " <b>تم شحن رصيدك بنجاح!</b>",
                "",
                f" المبلغ: <b>{_fmt(new_amount)}</b>",
                f" رقم الطلب: <code>{req_id}</code>",
            ]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" عرض الملف الشخصي", callback_data="profile", style="primary")]
            ]),
        )
    except Exception as e:
        logger.warning("User notify failed: %s", e)
