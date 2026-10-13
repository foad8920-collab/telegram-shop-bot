"""
Support Bot for Yemen E-Platform

منطق العمل:
1. العميل يراسل @YemenServiceBot في الخاص
2. البوت ينشئ Topic خاص بالعميل في مجموعة "فريق الدعم"
3. رسائل العميل تُنقل إلى Topic الخاص به
4. ردود فريق الدعم من Topic تُنقل إلى العميل في الخاص
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== الإعدادات =====
SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "0"))

if not SUPPORT_BOT_TOKEN:
    raise RuntimeError("SUPPORT_BOT_TOKEN is not set")
if not SUPPORT_GROUP_ID:
    raise RuntimeError("SUPPORT_GROUP_ID is not set")

# ===== التسجيل =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("support_bot")

# ===== البوت والموزع =====
bot = Bot(
    token=SUPPORT_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ===== تخزين مؤقت: user_id  topic_id =====
# (في الإنتاج، استخدم قاعدة بيانات)
_user_topics: dict[int, int] = {}


# ===== أوامر العميل =====
@router.message(CommandStart())
async def cmd_start(message: Message):
    """ترحيب بالعميل + زر بدء المحادثة."""
    user = message.from_user
    if not user:
        return

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text=" بدء محادثة مع الدعم",
        callback_data="start_support",
    ))

    await message.answer(
        f" <b>أهلاً بك {user.full_name}!</b>\n\n"
        "هذا بوت الدعم الفني لمنصة اليمن الإلكترونية.\n"
        "اضغط الزر أدناه لبدء محادثة مع فريق الدعم.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "start_support")
async def start_support(call: CallbackQuery):
    """إنشاء Topic جديد للعميل أو استخدام الموجود."""
    if not call.from_user or not call.message:
        return

    user = call.from_user
    user_id = user.id

    # تحقق إذا كان للعميل topic موجود
    if user_id in _user_topics:
        topic_id = _user_topics[user_id]
        await call.answer("المحادثة مفتوحة بالفعل ", show_alert=False)
        await call.message.answer(
            f" محادثتك مفتوحة. اكتب رسالتك وسيتم توصيلها للدعم."
        )
        return

    # أنشئ Topic جديد
    try:
        topic = await bot.create_forum_topic(
            chat_id=SUPPORT_GROUP_ID,
            name=f" {user.full_name} | {user_id}",
        )
        topic_id = topic.message_thread_id
        _user_topics[user_id] = topic_id

        logger.info("Created topic %s for user %s", topic_id, user_id)

        await call.answer(" تم فتح محادثة مع الدعم", show_alert=False)
        await call.message.answer(
            " <b>تم فتح محادثة مع فريق الدعم!</b>\n\n"
            " اكتب رسالتك الآن وسيتم توصيلها فورًا للدعم.\n"
            " سيصلك الرد في أقرب وقت."
        )

        # أرسل رسالة في الـ Topic لإعلام الفريق
        await bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=(
                f" <b>محادثة جديدة</b>\n"
                f" <b>الاسم:</b> {user.full_name}\n"
                f" <b>ID:</b> <code>{user_id}</code>\n"
                f" <b>Username:</b> @{user.username or 'لا يوجد'}\n"
            ),
        )

    except Exception as e:
        logger.exception("Failed to create topic: %s", e)
        await call.answer(
            " حدث خطأ، حاول مرة أخرى لاحقًا.",
            show_alert=True,
        )


# ===== استقبال رسائل العميل =====
@router.message(F.chat.type == "private")
async def handle_customer_message(message: Message):
    """نقل رسالة العميل إلى Topic الخاص به."""
    if not message.from_user:
        return

    user_id = message.from_user.id
    topic_id = _user_topics.get(user_id)

    if not topic_id:
        # لم يبدأ محادثة بعد
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(
            text=" بدء محادثة مع الدعم",
            callback_data="start_support",
        ))
        await message.answer(
            " <b>لم تبدأ محادثة مع الدعم بعد.</b>\n"
            "اضغط الزر أدناه لبدء المحادثة.",
            reply_markup=kb.as_markup(),
        )
        return

    # انقل الرسالة إلى الـ Topic
    try:
        # نسخ الرسالة بأنواعها
        await bot.copy_message(
            chat_id=SUPPORT_GROUP_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=topic_id,
        )

        # رد تأكيد خفيف للعميل
        if message.text or message.caption:
            await bot.send_message(
                chat_id=SUPPORT_GROUP_ID,
                message_thread_id=topic_id,
                text=(
                    f" <i>رسالة من {message.from_user.full_name}:</i>"
                ),
            )
    except Exception as e:
        logger.exception("Failed to forward message: %s", e)
        await message.answer(" حدث خطأ في إرسال رسالتك.")


# ===== استقبال ردود فريق الدعم =====
@router.message(F.chat.id == SUPPORT_GROUP_ID)
async def handle_support_reply(message: Message):
    """نقل رد فريق الدعم إلى العميل."""
    # تجاهل رسائل البوتات
    if message.from_user and message.from_user.is_bot:
        return

    # تجاهل الرسائل من General (بدون Topic)
    if not message.message_thread_id:
        return

    # تجاهل أوامر البوت
    if message.text and message.text.startswith("/"):
        return

    # ابحث عن العميل صاحب هذا الـ Topic
    customer_id: Optional[int] = None
    for uid, tid in _user_topics.items():
        if tid == message.message_thread_id:
            customer_id = uid
            break

    if not customer_id:
        return

    # انقل الرد للعميل
    try:
        await bot.copy_message(
            chat_id=customer_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        logger.info("Replied to customer %s", customer_id)
    except Exception as e:
        logger.exception("Failed to deliver reply: %s", e)
        # أخبر فريق الدعم بفشل الإرسال
        await message.reply(
            f" فشل إرسال الرد للعميل: {e}"
        )


# ===== تشغيل البوت =====
async def main():
    logger.info("Starting Support Bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Support Bot is running. Listening for messages...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Support Bot stopped.")
