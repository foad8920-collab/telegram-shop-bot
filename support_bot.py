"""
Support Bot for Yemen E-Platform

منطق العمل:
1. العميل يراسل @YemenServiceBot في الخاص
2. البوت ينشئ Topic خاص بالعميل في مجموعة "فريق الدعم"
3. رسائل العميل تُنقل إلى Topic الخاص به
4. ردود فريق الدعم من Topic تُنقل إلى العميل في الخاص

التحسينات:
- Topic محفوظ في قاعدة البيانات (support_topics)
- استمرارية المحادثات عبر إعادة التشغيل
- تخزين اسم العميل و username

المتغيرات المطلوبة (Environment):
- SUPPORT_BOT_TOKEN
- SUPPORT_GROUP_ID
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
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import SupportTopic

# ===== التسجيل =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("support_bot")

# ===== المتغيرات العالمية =====
bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None
router: Optional[Router] = None
SUPPORT_GROUP_ID: int = 0


# ============================================================
#  دوال قاعدة البيانات
# ============================================================
async def get_user_topic(user_id: int) -> Optional[int]:
    """جلب topic_id من قاعدة البيانات."""
    try:
        async with Database().session() as session:
            result = await session.execute(
                select(SupportTopic).where(
                    SupportTopic.user_id == user_id,
                    SupportTopic.is_active == True  # noqa: E712
                )
            )
            topic = result.scalar_one_or_none()
            return topic.topic_id if topic else None
    except Exception as e:
        logger.exception("get_user_topic failed: %s", e)
        return None


async def save_user_topic(
    user_id: int,
    topic_id: int,
    full_name: str = "",
    username: str = "",
) -> bool:
    """حفظ topic_id في قاعدة البيانات."""
    try:
        async with Database().session() as session:
            existing = await session.get(SupportTopic, user_id)
            if existing:
                existing.topic_id = topic_id
                existing.is_active = True
                existing.user_full_name = full_name
                existing.user_username = username
                existing.closed_at = None
            else:
                topic = SupportTopic(
                    user_id=user_id,
                    topic_id=topic_id,
                    user_full_name=full_name,
                    user_username=username,
                    is_active=True,
                )
                session.add(topic)
            await session.commit()
            return True
    except Exception as e:
        logger.exception("save_user_topic failed: %s", e)
        return False


async def close_user_topic(user_id: int) -> bool:
    """إنهاء المحادثة (إخفاء من القائمة النشطة)."""
    try:
        async with Database().session() as session:
            topic = await session.get(SupportTopic, user_id)
            if topic:
                topic.is_active = False
                from datetime import datetime, timezone
                topic.closed_at = datetime.now(timezone.utc)
                await session.commit()
                return True
            return False
    except Exception as e:
        logger.exception("close_user_topic failed: %s", e)
        return False


async def find_user_by_topic(topic_id: int) -> Optional[int]:
    """البحث عن user_id من topic_id."""
    try:
        async with Database().session() as session:
            result = await session.execute(
                select(SupportTopic).where(
                    SupportTopic.topic_id == topic_id,
                    SupportTopic.is_active == True  # noqa: E712
                )
            )
            topic = result.scalar_one_or_none()
            return topic.user_id if topic else None
    except Exception as e:
        logger.exception("find_user_by_topic failed: %s", e)
        return None


# ============================================================
#  بناء المعالجات
# ============================================================
def _setup_handlers(r: Router) -> None:
    """تسجيل كل المعالجات في الـ Router."""

    @r.message(CommandStart())
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

    @r.callback_query(F.data == "start_support")
    async def start_support(call: CallbackQuery):
        """إنشاء Topic جديد للعميل أو استخدام الموجود."""
        if not call.from_user or not call.message or bot is None:
            return

        user = call.from_user
        user_id = user.id

        # فحص إذا كان للعميل topic نشط
        existing_topic = await get_user_topic(user_id)

        if existing_topic:
            await call.answer("المحادثة مفتوحة بالفعل ", show_alert=False)
            await call.message.answer(
                " محادثتك مفتوحة. اكتب رسالتك وسيتم توصيلها للدعم."
            )
            return

        # أنشئ Topic جديد
        try:
            topic = await bot.create_forum_topic(
                chat_id=SUPPORT_GROUP_ID,
                name=f" {user.full_name} | {user_id}",
            )
            topic_id = topic.message_thread_id

            # احفظ في قاعدة البيانات
            saved = await save_user_topic(
                user_id=user_id,
                topic_id=topic_id,
                full_name=user.full_name or "",
                username=user.username or "",
            )

            if not saved:
                logger.error("Failed to save topic in DB for user %s", user_id)

            logger.info("Created topic %s for user %s", topic_id, user_id)

            await call.answer(" تم فتح محادثة مع الدعم", show_alert=False)
            await call.message.answer(
                " <b>تم فتح محادثة مع فريق الدعم!</b>\n\n"
                " اكتب رسالتك الآن وسيتم توصيلها فورًا للدعم.\n"
                " سيصلك الرد في أقرب وقت."
            )

            # رسالة ترحيبية في الـ Topic
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

    @r.message(F.chat.type == "private")
    async def handle_customer_message(message: Message):
        """نقل رسالة العميل إلى Topic الخاص به."""
        if not message.from_user or bot is None:
            return

        # تجاهل /start (معالجته خاصة)
        if message.text and message.text.startswith("/start"):
            return

        user_id = message.from_user.id
        topic_id = await get_user_topic(user_id)

        if not topic_id:
            # لم يبدأ محادثة
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

        try:
            await bot.copy_message(
                chat_id=SUPPORT_GROUP_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=topic_id,
            )
        except Exception as e:
            logger.exception("Failed to forward message: %s", e)
            await message.answer(" حدث خطأ في إرسال رسالتك.")

    @r.message(F.chat.id == SUPPORT_GROUP_ID)
    async def handle_support_reply(message: Message):
        """نقل رد فريق الدعم إلى العميل."""
        if bot is None:
            return

        if message.from_user and message.from_user.is_bot:
            return
        if not message.message_thread_id:
            return
        if message.text and message.text.startswith("/"):
            return

        customer_id = await find_user_by_topic(message.message_thread_id)

        if not customer_id:
            return

        try:
            await bot.copy_message(
                chat_id=customer_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            logger.info("Replied to customer %s", customer_id)
        except Exception as e:
            logger.exception("Failed to deliver reply: %s", e)
            await message.reply(f" فشل إرسال الرد للعميل: {e}")


# ============================================================
#  التشغيل
# ============================================================
async def main():
    """تشغيل بوت الدعم الفني."""
    global bot, dp, router, SUPPORT_GROUP_ID

    token = os.getenv("SUPPORT_BOT_TOKEN", "").strip()
    group_id_raw = os.getenv("SUPPORT_GROUP_ID", "0").strip()

    if not token:
        logger.warning("SUPPORT_BOT_TOKEN is not set  support bot will not start.")
        return

    try:
        group_id = int(group_id_raw)
    except ValueError:
        logger.warning("SUPPORT_GROUP_ID is invalid (%s)  support bot will not start.", group_id_raw)
        return

    if not group_id:
        logger.warning("SUPPORT_GROUP_ID is not set  support bot will not start.")
        return

    SUPPORT_GROUP_ID = group_id

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    router = Router()
    _setup_handlers(router)
    dp.include_router(router)

    logger.info("Starting Support Bot...")
    logger.info("Support group ID: %s", SUPPORT_GROUP_ID)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Support Bot is running. Listening for messages...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Support Bot stopped.")
