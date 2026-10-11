"""Admin-side handlers for local subscription orders."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.orm import noload

from bot.database.main import Database
from bot.database.models.main import (
    LocalSubscription, LocalSubscriptionOrder, LocalSubscriptionCode,
    User,
)
from bot.misc import EnvKeys
from bot.states import LocalBuy, AdminOrder


logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)


def _fmt(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}" + chr(36)


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
                if not p:
                    continue
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


async def _safe_answer(call: CallbackQuery):
    try:
        await call.answer()
    except TelegramBadRequest:
        pass


async def _replace_caption_or_text(call: CallbackQuery, new_caption: str, new_markup=None):
    """Try to edit caption (photo msg); fallback to text edit."""
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
#  APPROVE
# ============================================================



# ============================================================
#  Buyer data request messages
# ============================================================

_FIELD_PROMPTS: dict[str, tuple[str, str, str]] = {
    # key: (title_ar, icon, hint_ar)
    "email":       ("البريد الإلكتروني", "", "مثال: user@gmail.com"),
    "phone":       ("رقم الجوال",        "", "مثال: +967771234567"),
    "username":    ("اسم المستخدم",       "", "مثال: @username"),
    "telegram_id": ("معرّف تيليجرام",     "", "أرسل رقم ID الخاص بك"),
    "text":        ("المعلومة المطلوبة",  "", "أرسل النص المطلوب"),
    "none":        ("",                "", ""),
}


def _field_prompt(field_type: str) -> tuple[str, str, str]:
    return _FIELD_PROMPTS.get((field_type or "email").lower(), _FIELD_PROMPTS["email"])


@router.callback_query(F.data.startswith("local_order_approve:"))
async def local_order_approve_handler(call: CallbackQuery, dispatcher=None) -> None:
    """Admin clicked approve  deliver codes to the buyer."""
    await _safe_answer(call)

    if not _is_admin(call.from_user.id):
        await call.answer(" ليس لديك صلاحية.", show_alert=True)
        return

    try:
        order_id = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer("معرّف غير صالح.", show_alert=True)
        return

    admin_id = call.from_user.id
    buyer_id = None
    sub_name = ""
    total_str = ""
    codes_content: list[str] = []
    order_qty = 0
    _lookup_sub_id = None

    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(LocalSubscriptionOrder)
                .options(noload(LocalSubscriptionOrder.subscription))
                .where(LocalSubscriptionOrder.id == order_id)
                .with_for_update()
            )).scalar_one_or_none()

            if order is None:
                await call.answer("الطلب غير موجود.", show_alert=True)
                return

            if order.status != "pending":
                await call.answer(f"الطلب في حالة: {order.status} (لا يمكن تعديله).", show_alert=True)
                return

            sub = (await session.execute(
                select(LocalSubscription)
                .options(noload(LocalSubscription.codes))
                .where(LocalSubscription.id == order.subscription_id)
                .with_for_update()
            )).scalar_one_or_none()

            if sub is None:
                await call.answer("الاشتراك غير موجود.", show_alert=True)
                return

            delivery_type = (sub.delivery_type or "codes").lower()
            codes_per_purchase = max(1, int(sub.codes_per_purchase or 1))
            qty = int(order.quantity or 1)
            required_codes = qty * codes_per_purchase if delivery_type == "codes" else 0

            codes = []
            if required_codes > 0:
                result = await session.execute(
                    select(LocalSubscriptionCode)
                    .options(noload(LocalSubscriptionCode.subscription))
                    .where(
                        LocalSubscriptionCode.subscription_id == sub.id,
                        LocalSubscriptionCode.is_used == False,
                    )
                    .order_by(LocalSubscriptionCode.id)
                    .limit(required_codes)
                    .with_for_update()
                )
                codes = list(result.scalars().all())

                if len(codes) < required_codes:
                    await call.answer(
                        f" الأكواد المتاحة لا تكفي (المطلوب {required_codes}, المتاح {len(codes)}).",
                        show_alert=True,
                    )
                    return

            # الآن كل شيء جاهز  نفّذ العملية
            now = datetime.now(timezone.utc)
            order.status = "completed"
            order.admin_id = admin_id
            order.decided_at = now

            # اخصم الأكواد
            for code in codes:
                code.is_used = True
                code.used_by = order.user_id
                code.order_id = order.id
                code.used_at = now

            # خصم quantity (فقط للـ invite/manual)
            if delivery_type != "codes":
                sub.quantity = max(0, int(sub.quantity or 0) - qty)

            # سجّل المحتوى
            if codes:
                content = NL.join(f" {c.content}" for c in codes)
                order.delivered_content = content
                codes_content = [c.content for c in codes]

            await session.commit()

            buyer_id = order.user_id
            sub_name = str(sub.name)
            total_str = _fmt(order.total_price)
            order_qty = qty
            _lookup_sub_id = sub.id

    except Exception as e:
        logger.exception("Approve failed: %s", e)
        await call.answer(" حدث خطأ أثناء التأكيد. لم يتم التغيير.", show_alert=True)
        return

    # ---- أرسل للمشتري ----
    bot: Bot = call.bot

    # اقرأ delivery_type من الاشتراك (سنحتاج قراءته من order مرة أخرى)
    delivery_type_final = "codes"
    invite_field_final = "email"
    try:
        async with Database().session() as session2:
            sub2 = (await session2.execute(
                select(LocalSubscription).where(LocalSubscription.id == _lookup_sub_id)
            )).scalar_one_or_none()
            if sub2 is not None:
                delivery_type_final = (sub2.delivery_type or "codes").lower()
                invite_field_final = (sub2.invite_field_type or "email").lower()
    except Exception:
        pass

    if delivery_type_final in ("invite", "manual") and buyer_id:
        # لا نرسل أكواد  نطلب البيانات أو نُعلم بالتواصل
        try:
            if delivery_type_final == "invite" and invite_field_final != "none":
                title, icon, hint = _field_prompt(invite_field_final)
                prompt_text = NL.join([
                    " <b>تم تأكيد طلبك بنجاح!</b>",
                    "",
                    f" المنتج: <b>{sub_name}</b>",
                    f" الكمية: <b>{order_qty}</b>",
                    f" الإجمالي: <b>{total_str}</b>",
                    f" رقم الطلب: <code>#{order_id}</code>",
                    "",
                    f"{icon} <b>يرجى إرسال {title}:</b>",
                    f"<i>{hint}</i>",
                    "",
                    " <i>جاري تفعيل اشتراكك من قبل المشرف  يرجى انتظار تأكيده.</i>",
                ])
                # أرسل الرسالة للمشتري
                await bot.send_message(
                    chat_id=buyer_id,
                    text=prompt_text,
                    parse_mode="HTML",
                )

                # 1) نضع flag في DB (احتياطي/استرجاع)
                try:
                    async with Database().session() as _s2:
                        _o2 = (await _s2.execute(
                            select(LocalSubscriptionOrder)
                            .options(noload(LocalSubscriptionOrder.subscription))
                            .where(LocalSubscriptionOrder.id == order_id)
                        )).scalar_one_or_none()
                        if _o2 is not None:
                            _o2.awaiting_data = True
                            _o2.awaiting_field_type = invite_field_final
                            await _s2.commit()
                except Exception as _e:
                    logger.warning("failed to set awaiting_data: %s", _e)

                # 2) نضع حالة FSM للمشتري عبر StorageKey (الأسلوب الرسمي)
                if dispatcher is not None:
                    try:
                        from aiogram.fsm.storage.base import StorageKey
                        from bot.states import LocalBuy
                        _key = StorageKey(
                            bot_id=bot.id,
                            chat_id=buyer_id,
                            user_id=buyer_id,
                        )
                        await dispatcher.storage.set_state(
                            _key, LocalBuy.waiting_customer_data,
                        )
                        await dispatcher.storage.set_data(
                            _key,
                            {
                                "awaiting_order_id": order_id,
                                "awaiting_field_type": invite_field_final,
                            },
                        )
                        logger.info(
                            "FSM state set for user %s -> waiting_customer_data (order #%s)",
                            buyer_id, order_id,
                        )
                    except Exception as _e:
                        logger.warning("Failed to set FSM for buyer: %s", _e)
                else:
                    logger.warning("dispatcher not available  FSM state not set")
                # نطلب من المشرف أن ينتظر
                new_caption_pending = NL.join([
                    f" <b>بانتظار بيانات العميل  الطلب #{order_id}</b>",
                    "",
                    f" <b>{sub_name}</b> |  x{order_qty} |  {total_str}",
                    f" المشتري: <code>{buyer_id}</code>",
                    f"‍ المشرف: <code>{admin_id}</code>",
                    f" <b>بانتظار: {_field_prompt(invite_field_final)[0]}</b>",
                ])
                await _replace_caption_or_text(call, new_caption_pending, new_markup=None)
                return

            # manual أو invite بدون حقل
            manual_text = NL.join([
                " <b>تم تأكيد طلبك بنجاح!</b>",
                "",
                f" المنتج: <b>{sub_name}</b>",
                f" الكمية: <b>{order_qty}</b>",
                f" الإجمالي: <b>{total_str}</b>",
                f" رقم الطلب: <code>#{order_id}</code>",
                "",
                " <b>سيتم التواصل معك قريباً</b> لإتمام التسليم.",
            ])
            await bot.send_message(
                chat_id=buyer_id,
                text=manual_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
                ]),
            )
            new_caption_manual = NL.join([
                f" <b>تم التأكيد  الطلب #{order_id} (تسليم يدوي)</b>",
                "",
                f" <b>{sub_name}</b> |  x{order_qty} |  {total_str}",
                f" المشتري: <code>{buyer_id}</code>",
                f"‍ المشرف: <code>{admin_id}</code>",
                " <b>تواصل مع العميل يدوياً للتسليم</b>",
            ])
            await _replace_caption_or_text(call, new_caption_manual, new_markup=None)
            return
        except Exception as e:
            logger.warning("Failed to notify buyer %s: %s", buyer_id, e)

    # ---- مسار codes (كما هو) ----
    if buyer_id:
        try:
            if codes_content:
                # رسالة موجزة أولاً
                brief_text = NL.join([
                    " <b>تم تأكيد طلبك بنجاح!</b>",
                    "",
                    f" المنتج: <b>{sub_name}</b>",
                    f" الكمية: <b>{order_qty}</b>",
                    f" الإجمالي: <b>{total_str}</b>",
                    f" رقم الطلب: <code>#{order_id}</code>",
                    "",
                    " <b>الأكواد / المفاتيح في الملف المرفق </b>",
                    " <i>احتفظ بالملف  لن يُعرض مرة أخرى.</i>",
                ])
                await bot.send_message(
                    chat_id=buyer_id,
                    text=brief_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
                    ]),
                )

                # ملف نصي بالأكواد
                from datetime import datetime as _dt
                ts = _dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                sep = "=" * 50
                file_lines = [
                    sep,
                    "    الأكواد / المفاتيح",
                    f"   الطلب: #{order_id}",
                    f"   المنتج: {sub_name}",
                    f"   الكمية: {order_qty}",
                    f"   الإجمالي: {total_str}",
                    f"   التاريخ: {ts} UTC",
                    sep,
                    "",
                ]
                for idx, c in enumerate(codes_content, 1):
                    file_lines.append(f"{idx}. {c}")
                file_lines.append("")
                file_lines.append(sep)
                file_lines.append(" احتفظ بهذا الملف - لن يُعرض مرة أخرى")
                file_lines.append(sep)

                file_content = chr(10).join(file_lines)

                safe_name = "".join(ch for ch in str(sub_name) if ch.isalnum() or ch in "-_")[:30] or "order"
                filename = f"{safe_name}-order-{order_id}.txt"

                from aiogram.types import BufferedInputFile
                doc = BufferedInputFile(
                    file_content.encode("utf-8"),
                    filename=filename,
                )
                await bot.send_document(
                    chat_id=buyer_id,
                    document=doc,
                    caption=f" <b>مفاتيح الطلب #{order_id}</b>",
                    parse_mode="HTML",
                )
            else:
                # لا يوجد أكواد (invite / manual)
                buyer_text = NL.join([
                    " <b>تم تأكيد طلبك بنجاح!</b>",
                    "",
                    f" المنتج: <b>{sub_name}</b>",
                    f" الكمية: <b>{order_qty}</b>",
                    f" الإجمالي: <b>{total_str}</b>",
                    f" رقم الطلب: <code>#{order_id}</code>",
                    "",
                    " <b>سيتم التواصل معك قريبًا</b> لإتمام التسليم.",
                ])
                await bot.send_message(
                    chat_id=buyer_id,
                    text=buyer_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
                    ]),
                )
        except Exception as e:
            logger.warning("Failed to notify buyer %s: %s", buyer_id, e)

    # ---- حدّث رسالة المشرف ----
    new_caption = NL.join([
        f" <b>تم التأكيد  الطلب #{order_id}</b>",
        "",
        f" <b>{sub_name}</b> |  x{order_qty} |  {total_str}",
        f" المشتري: <code>{buyer_id}</code>",
        f"‍ المشرف: <code>{admin_id}</code>",
        " <b>تم تسليم الأكواد للمشتري</b>",
    ])
    await _replace_caption_or_text(call, new_caption, new_markup=None)


# ============================================================
#  REJECT
# ============================================================

@router.callback_query(F.data.startswith("local_order_reject:"))
async def local_order_reject_handler(call: CallbackQuery) -> None:
    """Admin clicked reject  notify the buyer."""
    await _safe_answer(call)

    if not _is_admin(call.from_user.id):
        await call.answer(" ليس لديك صلاحية.", show_alert=True)
        return

    try:
        order_id = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer("معرّف غير صالح.", show_alert=True)
        return

    admin_id = call.from_user.id
    buyer_id = None
    sub_name = ""
    total_str = ""
    order_qty = 0

    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(LocalSubscriptionOrder)
                .options(noload(LocalSubscriptionOrder.subscription))
                .where(LocalSubscriptionOrder.id == order_id)
                .with_for_update()
            )).scalar_one_or_none()

            if order is None:
                await call.answer("الطلب غير موجود.", show_alert=True)
                return

            if order.status != "pending":
                await call.answer(f"الطلب في حالة: {order.status} (لا يمكن تعديله).", show_alert=True)
                return

            now = datetime.now(timezone.utc)
            order.status = "rejected"
            order.admin_id = admin_id
            order.decided_at = now

            await session.commit()

            buyer_id = order.user_id
            order_qty = int(order.quantity or 0)
            total_str = _fmt(order.total_price)

            sub = (await session.execute(
                select(LocalSubscription).where(LocalSubscription.id == order.subscription_id)
            )).scalar_one_or_none()
            if sub is not None:
                sub_name = str(sub.name)

    except Exception as e:
        logger.exception("Reject failed: %s", e)
        await call.answer(" حدث خطأ. لم يتم التغيير.", show_alert=True)
        return

    # ---- أرسل للمشتري ----
    buyer_text = NL.join([
        " <b>نأسف، لم يتم تأكيد طلبك.</b>",
        "",
        f" المنتج: <b>{sub_name}</b>",
        f" الكمية: <b>{order_qty}</b>",
        f" المبلغ: <b>{total_str}</b>",
        f" رقم الطلب: <code>#{order_id}</code>",
        "",
        " قد يكون السبب: لم يتم استلام الحوالة، أو السند غير صالح.",
        " للاستفسار تواصل مع الدعم الفني.",
    ])

    bot: Bot = call.bot
    if buyer_id:
        try:
            await bot.send_message(
                chat_id=buyer_id,
                text=buyer_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
                ]),
            )
        except Exception as e:
            logger.warning("Failed to notify buyer %s: %s", buyer_id, e)

    # ---- حدّث رسالة المشرف ----
    new_caption = NL.join([
        f" <b>تم الرفض  الطلب #{order_id}</b>",
        "",
        f" المشتري: <code>{buyer_id}</code>",
        f"‍ المشرف: <code>{admin_id}</code>",
    ])
    await _replace_caption_or_text(call, new_caption, new_markup=None)

# ============================================================
#  ADMIN  send instructions to buyer (free-form text)
# ============================================================

@router.callback_query(F.data.startswith("admin_order_instr:"))
async def admin_order_instr_handler(call: CallbackQuery, state: FSMContext) -> None:
    """Admin clicked 'Send instructions'  enter FSM to type the message."""
    await _safe_answer(call)

    if not _is_admin(call.from_user.id):
        await call.answer(" ليس لديك صلاحية.", show_alert=True)
        return

    try:
        order_id = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer("معرّف غير صالح.", show_alert=True)
        return

    # verify order exists
    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(LocalSubscriptionOrder)
                .options(noload(LocalSubscriptionOrder.subscription))
                .where(LocalSubscriptionOrder.id == order_id)
            )).scalar_one_or_none()

            if order is None:
                await call.answer("الطلب غير موجود.", show_alert=True)
                return

            buyer_id = order.user_id
    except Exception as e:
        logger.exception("admin_order_instr: %s", e)
        await call.answer(" حدث خطأ.", show_alert=True)
        return

    await state.update_data(
        admin_order_id=order_id,
        admin_buyer_id=buyer_id,
    )
    await state.set_state(AdminOrder.awaiting_instructions)

    await call.message.answer(
        NL.join([
            f" <b>إرسال التعليمات  الطلب #{order_id}</b>",
            "",
            f" المشتري: <code>{buyer_id}</code>",
            "",
            " <b>اكتب الآن التعليمات التي ستُرسل للعميل:</b>",
            "",
            "<i>مثال: أخي الكريم، تم تفعيل اشتراكك، يرجى الدخول إلى البريد...</i>",
            "",
            " للإلغاء أرسل: <code>/cancel</code>",
        ]),
        parse_mode="HTML",
    )


@router.message(AdminOrder.awaiting_instructions, F.text)
async def admin_order_instr_message_handler(
    message: Message, state: FSMContext, dispatcher=None,
) -> None:
    """Admin sent the instructions  forward to buyer + mark order completed."""
    if not _is_admin(message.from_user.id):
        await message.answer(" ليس لديك صلاحية.")
        await state.clear()
        return

    text = (message.text or "").strip()

    if text == "/cancel":
        await state.clear()
        await message.answer(" تم الإلغاء.")
        return

    if len(text) < 5:
        await message.answer(" النص قصير جداً. اكتب تعليمات أوضح.")
        return

    if len(text) > 3000:
        await message.answer(" النص طويل جداً (الحد 3000 حرف).")
        return

    data = await state.get_data()
    order_id = data.get("admin_order_id")
    buyer_id = data.get("admin_buyer_id")

    if not order_id or not buyer_id:
        await message.answer(" انتهت الجلسة.")
        await state.clear()
        return

    # حفظ التعليمات في DB + تحديث الحالة
    try:
        async with Database().session() as session:
            order = (await session.execute(
                select(LocalSubscriptionOrder)
                .options(noload(LocalSubscriptionOrder.subscription))
                .where(LocalSubscriptionOrder.id == order_id)
            )).scalar_one_or_none()

            if order is None:
                await message.answer(" الطلب غير موجود.")
                await state.clear()
                return

            # نخزّن التعليمات في admin_note (يوجد عمود جاهز)
            order.admin_note = text
            order.admin_id = message.from_user.id
            order.status = "completed"
            from datetime import datetime, timezone as _tz
            order.decided_at = order.decided_at or datetime.now(_tz.utc)

            await session.commit()
            sub_name = ""
            try:
                sub = (await session.execute(
                    select(LocalSubscription).where(LocalSubscription.id == order.subscription_id)
                )).scalar_one_or_none()
                if sub is not None:
                    sub_name = str(sub.name)
            except Exception:
                pass
    except Exception as e:
        logger.exception("Failed to save instructions: %s", e)
        await message.answer(" حدث خطأ. لم يتم الإرسال.")
        return

    # إرسال للمشتري
    bot: Bot = message.bot
    try:
        await bot.send_message(
            chat_id=buyer_id,
            text=NL.join([
                " <b>تم تفعيل اشتراكك بنجاح!</b>",
                "",
                f" المنتج: <b>{sub_name}</b>",
                f" رقم الطلب: <code>#{order_id}</code>",
                "",
                " <b>التعليمات:</b>",
                "",
                text,
                "",
                "",
                " <i>احتفظ برسالة التأكيد هذه.</i>",
            ]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=" رجوع للمتجر",
                    callback_data="shop",
                    style="primary",
                )],
            ]),
        )
    except Exception as e:
        logger.warning("Failed to send instructions to buyer: %s", e)
        await message.answer(f" تم الحفظ لكن فشل الإرسال: {e}")
        await state.clear()
        return

    await state.clear()

    await message.answer(
        NL.join([
            f" <b>تم إرسال التعليمات للمشتري</b>",
            "",
            f" الطلب: <code>#{order_id}</code>",
            f" المشتري: <code>{buyer_id}</code>",
            f" المنتج: <b>{sub_name}</b>",
            "",
            " <b>اكتملت العملية.</b>",
        ]),
        parse_mode="HTML",
    )

