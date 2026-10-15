from functools import partial

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.database.methods import (
    check_user_referrals, get_referral_earnings_stats, get_one_referral_earning, query_user_referrals,
    query_referral_earnings_from_user, query_all_referral_earnings,
)
from bot.database.methods.transactions import (
    get_referral_progress, get_user_rewards, claim_referral_reward,
    REFERRAL_REQUIRED_COUNT,
)
from bot.handlers.other import get_bot_info, display_name
from bot.keyboards import back, referral_system_keyboard, lazy_paginated_keyboard
from bot.misc import EnvKeys, LazyPaginator
from bot.i18n import localize, esc

router = Router()


@router.callback_query(F.data == "referral_system")
async def referral_callback_handler(call: CallbackQuery, state: FSMContext):
    """
    Show referral info, personal invite link, product progress, and rewards.
    """
    user_id = call.from_user.id
    referrals_count = await check_user_referrals(user_id)
    referral_percent = EnvKeys.REFERRAL_PERCENT
    bot_username = await get_bot_info(call)

    earnings_stats = await get_referral_earnings_stats(user_id)

    #  جلب العدادات والمكافآت
    progress = await get_referral_progress(user_id)
    rewards = await get_user_rewards(user_id)

    has_referrals = referrals_count > 0
    has_earnings = earnings_stats['total_earnings_count'] > 0
    has_progress = len(progress) > 0
    has_rewards = len(rewards) > 0

    # نص أساسي
    text = (
        f" <b>نظام الإحالة</b>\n"
        f"\n\n"
        f" <b>رابطك الخاص:</b>\n"
        f"<code>https://t.me/{bot_username}?start={user_id}</code>\n\n"
        f" <b>عدد الإحالات:</b> {referrals_count}\n"
    )

    #  عدادات المنتجات
    if has_progress:
        text += "\n<b> تقدمك نحو المكافآت:</b>\n"
        for item in progress[:5]:  # أول 5 منتجات فقط
            count = item["count"]
            required = item["required"]
            remaining = max(0, required - count)
            product = item["product_name"][:35]
            
            if count >= required:
                text += f" <b>{product}</b>  {count}/{required} \n"
            else:
                text += f" <b>{product}</b>  {count}/{required} (باقي {remaining})\n"

    #  المكافآت
    pending_rewards = [r for r in rewards if r["status"] == "pending"]
    if pending_rewards:
        text += f"\n<b> مكافآت جاهزة للاستلام:</b> {len(pending_rewards)}\n"

    # إحصائيات الأرباح القديمة
    if has_earnings:
        text += "\n" + localize('referrals.stats.template',
                                  active_count=earnings_stats['active_referrals_count'],
                                  total_earned=int(earnings_stats['total_amount']),
                                  total_original=int(earnings_stats['total_original_amount']),
                                  earnings_count=earnings_stats['total_earnings_count'],
                                  currency=EnvKeys.PAY_CURRENCY
                                  )

    # شرح النظام
    text += (
        f"\n\n"
        f" <b>كيف تربح منتجًا مجانًا؟</b>\n"
        f"1 شارك رابطك مع أصدقائك\n"
        f"2 عندما يشتري <b>{REFERRAL_REQUIRED_COUNT} أشخاص</b> نفس المنتج عبر رابطك\n"
        f"3 تحصل على <b>نفس المنتج مجانًا!</b> \n"
    )

    has_pending_rewards = len(pending_rewards) > 0
    markup = referral_system_keyboard(has_referrals, has_earnings, has_pending_rewards)
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data == "view_referrals")
async def view_referrals_handler(call: CallbackQuery, state: FSMContext):
    """
    Show a list of all user referrals with lazy loading.
    """
    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_user_referrals, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    # Check if there are any referrals
    total = await paginator.get_total_count()
    if total == 0:
        await call.message.edit_text(
            localize("referrals.list.empty"),
            reply_markup=back("referral_system")
        )
        return

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda referral_data: localize("referrals.item.format",
                                                 telegram_id=referral_data['telegram_id'],
                                                 total_earned=int(referral_data['total_earned']),
                                                 currency=EnvKeys.PAY_CURRENCY),
        item_callback=lambda referral_data: f"referral_earnings_{referral_data['telegram_id']}",
        page=0,
        back_cb="referral_system",
        nav_cb_prefix="referrals_page_"
    )

    await call.message.edit_text(
        localize("referrals.list.title"),
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("referrals_page_"))
async def referrals_pagination_handler(call: CallbackQuery, state: FSMContext):
    """
    Pagination processing for the referral list with lazy loading.
    """
    try:
        page = int(call.data.split("_")[-1])
    except (ValueError, IndexError):
        await call.answer(localize("errors.pagination_invalid"))
        return

    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_user_referrals, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda referral_data: localize("referrals.item.format",
                                                 telegram_id=referral_data['telegram_id'],
                                                 total_earned=int(referral_data['total_earned']),
                                                 currency=EnvKeys.PAY_CURRENCY),
        item_callback=lambda referral_data: f"referral_earnings_{referral_data['telegram_id']}",
        page=page,
        back_cb="referral_system",
        nav_cb_prefix="referrals_page_"
    )

    await call.message.edit_text(
        localize("referrals.list.title"),
        reply_markup=markup
    )


async def _show_ref_earnings_page(call: CallbackQuery, referral_id: int, page: int):
    """Render one page of the caller's earnings from a specific referral."""
    user_id = call.from_user.id
    paginator = LazyPaginator(
        partial(query_referral_earnings_from_user, user_id, referral_id), per_page=10,
    )

    name = esc(await display_name(call.message.bot, referral_id))

    if page == 0 and await paginator.get_total_count() == 0:
        await call.message.edit_text(
            localize("referral.earnings.empty", id=referral_id, name=name),
            reply_markup=back("view_referrals")
        )
        return

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda earning: localize("referral.earning.format",
                                           amount=int(earning.amount),
                                           currency=EnvKeys.PAY_CURRENCY,
                                           date=earning.created_at.strftime("%d.%m.%Y %H:%M"),
                                           original_amount=int(earning.original_amount)),
        item_callback=lambda earning: f"earning_detail:{earning.id}:referral_earnings_{referral_id}",
        page=page,
        back_cb="view_referrals",
        nav_cb_prefix=f"ref-earn_{referral_id}_"
    )

    title_text = localize("referral.earnings.title", telegram_id=referral_id, name=name)
    await call.message.edit_text(title_text, reply_markup=markup)


@router.callback_query(F.data.startswith("referral_earnings_"))
async def referral_earnings_handler(call: CallbackQuery, state: FSMContext):
    """Show all earnings from a specific referral with lazy loading."""
    try:
        referral_id = int(call.data.split("_")[-1])
    except (ValueError, IndexError):
        await call.answer(localize("errors.invalid_data"))
        return

    await _show_ref_earnings_page(call, referral_id, 0)


@router.callback_query(F.data.startswith("ref-earn_"))
async def referral_earnings_pagination_handler(call: CallbackQuery, state: FSMContext):
    """Pagination for a single referral's earnings. Format: ref-earn_{referral_id}_{page}"""
    try:
        _prefix, referral_id, page = call.data.split("_")
        referral_id, page = int(referral_id), int(page)
    except (ValueError, IndexError):
        await call.answer(localize("errors.pagination_invalid"))
        return

    await _show_ref_earnings_page(call, referral_id, page)


@router.callback_query(F.data == "view_all_earnings")
async def view_all_earnings_handler(call: CallbackQuery, state: FSMContext):
    """
    Show all user referral earnings with lazy loading.
    """
    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_all_referral_earnings, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    # Check if there are any earnings
    total = await paginator.get_total_count()
    if total == 0:
        await call.message.edit_text(
            localize("all.earnings.empty"),
            reply_markup=back("referral_system")
        )
        return

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda earning: localize("all.earning.format",
                                           amount=int(earning.amount),
                                           currency=EnvKeys.PAY_CURRENCY,
                                           referral_id=earning.referral_id,
                                           date=earning.created_at.strftime("%d.%m.%Y %H:%M")),
        item_callback=lambda earning: f"earning_detail:{earning.id}:view_all_earnings",
        page=0,
        back_cb="referral_system",
        nav_cb_prefix="all_earnings_page_"
    )

    await call.message.edit_text(
        localize("all.earnings.title"),
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("all_earnings_page_"))
async def all_earnings_pagination_handler(call: CallbackQuery, state: FSMContext):
    """
    Pagination processing for all referral earnings with lazy loading.
    """
    try:
        page = int(call.data.split("_")[-1])
    except (ValueError, IndexError):
        await call.answer(localize("errors.pagination_invalid"))
        return

    user_id = call.from_user.id

    # Create paginator
    query_func = partial(query_all_referral_earnings, user_id)
    paginator = LazyPaginator(query_func, per_page=10)

    markup = await lazy_paginated_keyboard(
        paginator=paginator,
        item_text=lambda earning: localize("all.earning.format",
                                           amount=int(earning.amount),
                                           currency=EnvKeys.PAY_CURRENCY,
                                           referral_id=earning.referral_id,
                                           date=earning.created_at.strftime("%d.%m.%Y %H:%M")),
        item_callback=lambda earning: f"earning_detail:{earning.id}:all_earnings_page_{page}",
        page=page,
        back_cb="referral_system",
        nav_cb_prefix="all_earnings_page_"
    )

    await call.message.edit_text(
        localize("all.earnings.title"),
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("earning_detail:"))
async def earning_detail_handler(call: CallbackQuery, state: FSMContext):
    """
    Show details for one of the caller's own referral earnings.

    Scoped to the caller's referrer_id so one user cannot read another's
    earning rows by enumerating ids.
    """
    try:
        trash, earning_id, back_data = call.data.split(':', 2)
        earning_id = int(earning_id)
    except (ValueError, IndexError):
        await call.answer(localize("errors.invalid_data"), show_alert=True)
        return

    earning_info = await get_one_referral_earning(earning_id, referrer_id=call.from_user.id)
    if not earning_info:
        await call.answer(localize("errors.invalid_data"), show_alert=True)
        return

    await call.message.edit_text(localize('referral.item.info',
                                          id=earning_id,
                                          telegram_id=earning_info['referral_id'],
                                          name=esc(await display_name(
                                              call.message.bot, earning_info['referral_id'])),
                                          amount=earning_info['amount'],
                                          currency=EnvKeys.PAY_CURRENCY,
                                          date=earning_info['created_at'].strftime("%d.%m.%Y %H:%M"),
                                          original_amount=earning_info['original_amount']
                                          ), reply_markup=back(back_data))
    await state.clear()

# ============================================================
#   معالجات المكافآت
# ============================================================

@router.callback_query(F.data == "claim_rewards")
async def claim_rewards_handler(call: CallbackQuery, state: FSMContext):
    """استلام كل المكافآت المعلقة تلقائيًا."""
    user_id = call.from_user.id
    
    # جلب المكافآت المعلقة
    rewards = await get_user_rewards(user_id)
    pending = [r for r in rewards if r["status"] == "pending"]
    
    if not pending:
        await call.answer("لا توجد مكافآت للاستلام حاليًا.", show_alert=True)
        return
    
    # استلام كل مكافأة
    claimed = 0
    for reward in pending:
        ok, msg = await claim_referral_reward(user_id, reward["id"])
        if ok:
            claimed += 1
    
    # رسالة نجاح
    if claimed > 0:
        await call.answer(f" تم استلام {claimed} مكافأة!", show_alert=True)
    else:
        await call.answer(" حدث خطأ، حاول مرة أخرى.", show_alert=True)
    
    # إعادة عرض الصفحة الرئيسية
    await referral_callback_handler(call, state)


@router.callback_query(F.data == "view_rewards_history")
async def view_rewards_history_handler(call: CallbackQuery, state: FSMContext):
    """عرض كل المكافآت (المعلقة والمُسلّمة)."""
    user_id = call.from_user.id
    rewards = await get_user_rewards(user_id)
    
    if not rewards:
        await call.message.edit_text(
            " <b>سجل المكافآت</b>\n\n"
            "لا توجد مكافآت بعد.\n\n"
            " شارك رابطك مع أصدقائك، وعندما يشتري 3 أشخاص نفس المنتج  تحصل عليه مجانًا! ",
            reply_markup=back("referral_system"),
            parse_mode="HTML",
        )
        return
    
    text = " <b>سجل المكافآت</b>\n"
    text += "\n\n"
    
    for r in rewards[:20]:  # أول 20
        status_emoji = "" if r["status"] == "delivered" else ""
        status_text = "مُسلّمة" if r["status"] == "delivered" else "جاهزة للاستلام"
        product = r["product_name"][:40]
        text += f"{status_emoji} <b>{product}</b>\n"
        text += f"    {status_text}\n\n"
    
    await call.message.edit_text(
        text,
        reply_markup=back("referral_system"),
        parse_mode="HTML",
    )

