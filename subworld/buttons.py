"""Panel-driven main menu (inline buttons under the welcome message).

The menu is an INLINE keyboard attached to the welcome message, so it
sits in the messages area above the input box - exactly like the Yemen
bot's menu. (A reply keyboard is rendered by the client below the
input box, which is why the menu is inline, not a reply keyboard.)

Nothing about the main menu lives in code: the menu is built from
BotButton rows in the database, so the user adds, edits, deletes,
reorders, renames and recolours every button from the admin panel.
The only code-side piece is the one-time seed below, which runs when
the table is empty - after that the panel owns the menu completely.

A press arrives as the button's callback_data (shop / profile /
choose_language / rules / ...), handled by the catalog router; a
button with a url opens that link directly, no handler needed.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import types
from sqlalchemy import delete, func, select

from .button_models import (
    BUTTON_STYLES,
    DEFAULT_LABELS,
    MAIN_MENU_NAME,
    BotButton,
    BotLabel,
    BotRule,
    label_text,
    load_labels,
)
from .db import get_session_factory
from .keyboards import TEXTS
from .models import Order, SubworldUser

logger = logging.getLogger("subworld.buttons")


def _session():
    return get_session_factory()()


async def load_menu_buttons(session, menu_name: str = MAIN_MENU_NAME):
    rows = (
        await session.execute(
            select(BotButton)
            .where(
                BotButton.menu_name == menu_name,
                BotButton.is_active == True,  # noqa: E712
            )
            .order_by(BotButton.sort_order, BotButton.id)
        )
    ).scalars().all()
    return rows


def build_inline_kb(buttons, lang: str) -> types.InlineKeyboardMarkup:
    """Inline rows from BotButton rows: a full_width button takes its
    own row, otherwise two buttons share a row, in sort_order. A
    button with a url opens the link; otherwise it sends its
    callback_data. Rows with neither are skipped."""
    rows: list[list[types.InlineKeyboardButton]] = []
    pending: list[types.InlineKeyboardButton] = []

    def flush():
        nonlocal pending
        if pending:
            rows.append(pending)
            pending = []

    for b in buttons:
        kwargs: dict = {}
        text = b.text_for(lang).strip()
        if not text:
            logger.warning("SubWorld menu button id=%s has no text, skipped", b.id)
            continue
        style = (b.style or "").strip().lower()
        if style in BUTTON_STYLES:
            kwargs["style"] = style
        # A custom-emoji id must be a plain ASCII number (Telegram
        # rejects anything else and kills the WHOLE keyboard with
        # "icon_custom_emoji_id must be a valid Number"). A bad value
        # typed in the panel must never break the menu, so it is
        # dropped with a warning instead of being sent.
        icon = (b.icon_custom_emoji_id or "").strip()
        if icon:
            if icon.isascii() and icon.isdigit():
                kwargs["icon_custom_emoji_id"] = icon
            else:
                logger.warning(
                    "SubWorld menu button %r has an invalid icon %r (must be a numeric custom-emoji id), ignored",
                    b.button_text,
                    icon,
                )
        url = (b.url or "").strip()
        action = (b.callback_data or "").strip()
        if url:
            btn = types.InlineKeyboardButton(text=b.text_for(lang), url=url, **kwargs)
        elif action:
            btn = types.InlineKeyboardButton(
                text=b.text_for(lang), callback_data=action, **kwargs
            )
        else:
            logger.warning("SubWorld menu button %r has no url or callback_data, skipped", b.button_text)
            continue
        if b.full_width:
            flush()
            rows.append([btn])
        else:
            pending.append(btn)
            if len(pending) == 2:
                flush()
    flush()
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def ensure_default_buttons() -> None:
    """Seed the main menu once, when the table is empty (Yemen-like
    layout: store full-width green, then pairs). The panel can change
    or delete every seeded row afterwards."""
    async with _session() as session:
        count = (
            await session.execute(select(func.count()).select_from(BotButton))
        ).scalar_one()
        if count:
            return
        session.add_all(
            [
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="🛍️ المتجر العام",
                    button_text_en="🛍️ Store",
                    callback_data="shop",
                    style="success",
                    full_width=True,
                    sort_order=1,
                ),
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="💰 رصيدي",
                    button_text_en="💰 Balance",
                    callback_data="profile",
                    style="primary",
                    sort_order=2,
                ),
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="🌐 اللغة",
                    button_text_en="🌐 Language",
                    callback_data="choose_language",
                    style="primary",
                    sort_order=3,
                ),
                BotButton(
                    menu_name=MAIN_MENU_NAME,
                    button_text="📜 القوانين",
                    button_text_en="📜 Rules",
                    callback_data="rules",
                    style="primary",
                    sort_order=4,
                ),
            ]
        )
        await session.commit()
        logger.info("SubWorld default menu buttons seeded (4 buttons)")


async def ensure_default_labels() -> None:
    """Seed the fixed store-button labels once, when the table is
    empty. The panel can change every seeded row afterwards."""
    async with _session() as session:
        count = (
            await session.execute(select(func.count()).select_from(BotLabel))
        ).scalar_one()
        if count:
            return
        session.add_all(
            [
                BotLabel(key=key, text_ar=ar, text_en=en, style=style)
                for key, (ar, en, style) in DEFAULT_LABELS.items()
            ]
        )
        await session.commit()
        logger.info("SubWorld default button labels seeded (%d labels)", len(DEFAULT_LABELS))


# The final SubWorld rules (Arabic, English), adapted from the Yemen
# bot's 18 rules: SubWorld name, USD + Binance/crypto/wallet payment,
# no Yemeni-law reference in disputes, no email anywhere (everything
# runs through the Telegram account or Binance), and no cash refunds
# at all - a problem is compensated with wallet credit instead.
# Rule 1's Arabic text is the sentinel: if it is already in the
# table, the adapted set is in place and nothing is touched.
SUBWORLD_RULES: tuple[tuple[str, str], ...] = (
    (
        "SubWorld — عالم الاشتراكات وسيط لبيع الاشتراكات الرقمية، وهو جهة مستقلة وغير تابعة لـ Google أو OpenAI أو أي مزوّد آخر، وجميع العلامات التجارية ملك لأصحابها.",
        "SubWorld is an independent reseller of digital subscriptions and is not affiliated with Google, OpenAI, or any other provider. All trademarks belong to their respective owners.",
    ),
    (
        "استخدام البوت والشراء منه يعني موافقتك الكاملة على هذه القوانين، ومن لا يوافق عليها يجب عليه عدم إتمام الطلب.",
        "Using the bot and purchasing from it means you fully accept these rules. If you do not agree, do not complete your order.",
    ),
    (
        "يجب أن يكون عمر العميل 18 سنة فأكثر، أو أن يتم الشراء بموافقة ولي أمره، وهو مسؤول عن حسابه وطلباته.",
        "You must be 18 years or older, or purchase with a guardian's consent. You are responsible for your account and your orders.",
    ),
    (
        "العميل مسؤول عن صحة بياناته، مثل معرف حسابه في تليجرام ورقم التواصل، وأي خطأ في البيانات يتحمل نتيجته.",
        "You are responsible for the accuracy of your details, such as your Telegram username and contact number. Any mistake in your details is your responsibility.",
    ),
    (
        "الأسعار المعلنة وقت الطلب هي المعتمدة، والدفع مقدم بالدولار الأمريكي عبر طرق الدفع المتاحة في البوت (بينانس، العملات الرقمية، المحفظة)، ولا يبدأ تنفيذ الطلب قبل تأكيد الدفع.",
        "The prices shown at the time of ordering are the applicable ones. Payment is made in advance in US dollars through the payment methods available in the bot (Binance, cryptocurrency, wallet), and your order is not processed until payment is confirmed.",
    ),
    (
        "التسليم رقمي عبر البوت خلال المدة المعلنة، ويجب على العميل فحص المنتج فور استلامه والإبلاغ عن أي مشكلة خلال 24 ساعة.",
        "Delivery is digital, through the bot, within the stated time. You must check the product immediately upon receipt and report any problem within 24 hours.",
    ),
    (
        "الأكواد والحسابات المسلّمة سرية خاصة بالعميل، ومشاركتها مع غيره أو فقدانها بسبب إهماله تقع على مسؤوليته ولا يترتب عليها تعويض.",
        "Delivered codes and accounts are confidential and for your use only. Sharing them with others or losing them through your own negligence is your responsibility and does not entitle you to compensation.",
    ),
    (
        "يلتزم العميل بشروط المزوّد الأصلي للخدمة، ويمنع استخدام الاشتراك في أي نشاط غير قانوني أو احتيال أو إرسال مزعج أو انتهاك لحقوق الغير.",
        "You must comply with the original provider's terms of service. Using a subscription for any illegal activity, fraud, spam, or infringement of others' rights is prohibited.",
    ),
    (
        "إذا أوقف المزوّد الحساب بسبب سوء استخدام العميل أو مخالفته لشروط المزوّد، فلا يشمل ذلك الضمان ولا يحق للعميل أي تعويض.",
        "If the provider suspends the account because of your misuse or violation of the provider's terms, this is not covered by the warranty and you are not entitled to any compensation.",
    ),
    (
        "لا يجوز إعادة بيع الاشتراك أو مشاركته أو تأجيره لغير العميل، إلا إذا كان نوع الاشتراك يسمح بذلك صراحة.",
        "You may not resell, share, or rent out a subscription to anyone else, unless the subscription type explicitly allows it.",
    ),
    (
        "الضمان يشمل عيبا في التسليم نفسه خلال فترة الضمان المعلنة فقط، ولا يشمل انقطاع خدمة المزوّد أو تغيير مزاياه أو سوء استخدام العميل.",
        "The warranty covers only a defect in the delivery itself, during the stated warranty period. It does not cover interruptions of the provider's service, changes to its features, or your misuse.",
    ),
    (
        "المنتجات الرقمية بعد تسليمها وتفعيلها لا تسترد، ويستثنى من ذلك عدم التسليم أو منتج معيب لم يعمل من الأساس، بعد إثبات ذلك خلال 48 ساعة، ويكون الحل استبدالا أو إضافة المبلغ إلى رصيد محفظة العميل حسب تقدير الإدارة.",
        "Digital products are non-refundable after delivery and activation, except in the case of non-delivery or a product that never worked, proven within 48 hours. The remedy is a replacement or adding the amount to the customer's wallet balance, at management's discretion.",
    ),
    (
        "البوت لا يضمن استمرار خدمة أي طرف ثالث، وإذا أوقف المزوّد خدمته كليا يقتصر أي تعويض، إن وجد، على إضافة قيمة المدة المتبقية غير المستخدمة إلى رصيد محفظة العميل حسب تقدير الإدارة.",
        "The bot does not guarantee the continued service of any third party. If a provider discontinues its service entirely, any compensation, if applicable, is limited to adding the value of the remaining unused period to the customer's wallet balance, at management's discretion.",
    ),
    (
        "للإدارة حق تعديل الأسعار والمنتجات وهذه القوانين بإشعار داخل البوت، والطلبات المؤكدة قبل التعديل تبقى على سعرها وشروطها.",
        "Management may change prices, products, and these rules, with notice inside the bot. Orders confirmed before a change keep their price and terms.",
    ),
    (
        "بيانات العميل تستخدم فقط لتنفيذ الطلب وتقديم الدعم، ولا تباع ولا تشارك إلا عند الضرورة لتنفيذ الطلب أو بموجب طلب قانوني.",
        "Your details are used only to fulfil your order and provide support. They are never sold, and are shared only when necessary to fulfil the order or when required by law.",
    ),
    (
        "للإدارة حق رفض أو إيقاف أي طلب أو حساب عند إساءة الاستخدام أو الاحتيال أو مخالفة هذه القوانين، دون إخلال برصيد محفظة العميل غير المستخدم.",
        "Management may refuse or suspend any order or account in cases of misuse, fraud, or violation of these rules, without prejudice to the customer's unused wallet balance.",
    ),
    (
        "أقصى مسؤولية للبوت عن أي طلب هي قيمة ذلك الطلب نفسه، ولا يتحمل أي أضرار غير مباشرة أو خسارة أرباح أو بيانات.",
        "The bot's maximum liability for any order is the value of that order itself. It is not liable for any indirect damages or loss of profits or data.",
    ),
    (
        "عند أي نزاع يجب التواصل مع الدعم عبر البوت أولا مع الاحتفاظ برقم الطلب وإثبات الدفع، وتحل النزاعات وديا.",
        "In case of any dispute, contact support through the bot first, keeping your order number and proof of payment. Disputes are resolved amicably.",
    ),
    (
        "الدفع بالعملات الرقمية نهائي بعد تأكيده على الشبكة. يجب إرسال المبلغ الدقيق إلى العنوان الدقيق الظاهر في الطلب وعلى نفس الشبكة؛ والإرسال إلى عنوان خاطئ أو شبكة مختلفة أو بمبلغ مختلف مسؤولية العميل ولا يسترد.",
        "Cryptocurrency payments are final once confirmed on the network. You must send the exact amount to the exact address shown in the order, on the same network. Sending to a wrong address, a different network, or a different amount is your responsibility and is not refundable.",
    ),
    (
        "رصيد المحفظة غير قابل للتحويل لعميل آخر ولا يسترد نقدا، ويُضاف الرصيد بعد تأكيد الدفع.",
        "Wallet balance cannot be transferred to another customer and is not refundable in cash. Balance is credited after payment is confirmed.",
    ),
    (
        "الدعم متوفر باللغتين العربية والإنجليزية.",
        "Support is available in Arabic and English.",
    ),
    (
        "جميع الأسعار بالدولار الأمريكي، وأي سعر يظهر بعملة أخرى فهو استرشادي فقط وقد يختلف حسب سعر الصرف.",
        "All prices are in US dollars. Any price shown in another currency is indicative only and may vary with the exchange rate.",
    ),
    (
        "لا يستطيع أي عميل استرداد المبالغ نقدا في حال حصل أي خلل؛ بل تُضاف المبالغ إلى رصيد محفظته في البوت ويستطيع شراء أي منتج آخر بها.",
        "No customer can receive a cash refund if any problem occurs. Instead, the amount is added to their wallet balance in the bot, and they can use it to buy any other product.",
    ),
)

_ADAPTED_RULES_SENTINEL = SUBWORLD_RULES[0][0]


async def ensure_rules_seeded() -> int:
    """Seed the adapted SubWorld rules once, replacing the old set.

    The first package (rules-v1) copied the 18 Yemen rules verbatim and
    the user deployed it, so the table already holds them. The first
    time this version runs it wipes the whole table and inserts the
    adapted set (SUBWORLD_RULES, Arabic + English). Afterwards the
    sentinel (adapted rule 1) is present, so later starts touch
    nothing - rules the user adds or edits in the panel are safe.
    Returns the number of rules written (0 if already in place or
    skipped)."""
    try:
        async with _session() as session:
            present = (await session.execute(
                select(BotRule.id)
                .where(BotRule.text_ar == _ADAPTED_RULES_SENTINEL)
                .limit(1)
            )).first()
            if present is not None:
                return 0
            await session.execute(delete(BotRule))
            for i, (text_ar, text_en) in enumerate(SUBWORLD_RULES, start=1):
                session.add(BotRule(
                    text_ar=text_ar,
                    text_en=text_en,
                    sort_order=i,
                    is_active=True,
                ))
            await session.commit()
        logger.info("SubWorld rules seeded: %s rules (adapted set)", len(SUBWORLD_RULES))
        return len(SUBWORLD_RULES)
    except Exception as e:
        logger.warning("SubWorld rules seeding failed: %s", e)
        return 0


async def _welcome_stats(user_id: int):
    """(balance_usd, purchase_count) from the DB; (None, None) when the
    database is unavailable. Purchases count every order (delivered
    and pending - the balance is already deducted when the order is
    created), like the Yemen bot's all-sources counter."""
    try:
        async with _session() as session:
            user = await session.get(SubworldUser, user_id)
            balance = Decimal(str(user.balance_usd or 0)) if user else Decimal("0")
            count = (
                await session.execute(
                    select(func.count()).select_from(Order).where(Order.user_id == user_id)
                )
            ).scalar_one()
            return balance, count
    except Exception:
        logger.exception("SubWorld welcome stats failed")
        return None, None


def _welcome_text(lang: str, name: str, balance: Decimal, count: int) -> str:
    """Welcome exactly like the Yemen bot's: greeting + balance +
    purchases + 'choose from the menu below' (USD only)."""
    if lang == "en":
        greeting = f"🎉 Welcome, {name}!" if name else "🎉 Welcome!"
        purchases = "1 purchase" if count == 1 else f"{count} purchases"
        return (
            f"{greeting}\n\n"
            f"💎 Balance: ${balance:.2f}\n"
            f"🛍️ Purchases: {purchases}\n\n"
            "Choose from the menu below:"
        )
    greeting = f"🎉 أهلاً بك! {name}" if name else "🎉 أهلاً بك!"
    if count == 1:
        purchases = "مشترى واحد"
    elif count == 2:
        purchases = "مشتريان"
    else:
        purchases = str(count)
    return (
        f"{greeting}\n\n"
        f"💎 رصيدك: ${balance:.2f}\n"
        f"🛍️ مشترياتك: {purchases}\n\n"
        "اختر من القائمة أدناه:"
    )


async def _main_menu_content(lang: str, user):
    """(welcome text, inline keyboard) for the main menu. Falls back
    to the static welcome + built-in inline menu when the database is
    unavailable or the panel has no buttons. `user` is the Telegram
    user (needed because callback.message.from_user is the bot)."""
    from .keyboards import main_menu_kb

    tg_user = user
    name = ""
    if tg_user is not None:
        name = (tg_user.first_name or tg_user.username or "").strip()
    try:
        async with _session() as session:
            buttons = await load_menu_buttons(session)
        if buttons:
            balance, count = await _welcome_stats(tg_user.id)
            if balance is None:
                text = TEXTS[lang]["welcome"]
            else:
                text = _welcome_text(lang, name, balance, count)
            return text, build_inline_kb(buttons, lang)
    except Exception:
        logger.exception("SubWorld main menu from panel failed, using inline fallback")
    return TEXTS[lang]["welcome"], main_menu_kb(lang)


async def send_main_menu(message: types.Message, lang: str, user=None) -> None:
    """Welcome (Yemen-style) + the panel-driven inline menu, as a new
    message (used by /start).

    The welcome is sent WITH the inline menu attached. The old reply
    keyboard left by the pre-v2 menu is removed by a second, transient
    message carrying ReplyKeyboardRemove, deleted at once - a message
    has one reply_markup, and trying to attach the inline menu to the
    remove-message afterwards fails with Telegram's "message can't be
    edited" (seen twice in the Render log) and the fallback then sent
    a duplicate welcome. A reply keyboard stays docked below the input
    box until the bot removes it, so this one-time removal stays."""
    tg_user = user or message.from_user
    text, kb = await _main_menu_content(lang, tg_user)
    await message.answer(text, reply_markup=kb)
    try:
        tmp = await message.answer(
            "✅ تم تحديث القائمة", reply_markup=types.ReplyKeyboardRemove()
        )
        await tmp.delete()
    except Exception:
        logger.debug("SubWorld old keyboard removal skipped", exc_info=True)


async def edit_main_menu(callback: types.CallbackQuery, lang: str) -> None:
    """Turn the current message back into the welcome + inline menu
    (used by 'back to main menu' and after choosing a language)."""
    from aiogram.exceptions import TelegramBadRequest

    text, kb = await _main_menu_content(lang, callback.from_user)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
        logger.debug("SubWorld main-menu edit skipped: message not modified")


async def _send_rules(callback: types.CallbackQuery, lang: str) -> None:
    """Rules as an edit of the current message, with a back button to
    the main menu (its text/colour come from the panel labels)."""
    labels = dict(DEFAULT_LABELS)
    try:
        async with _session() as session:
            rows = (
                await session.execute(
                    select(BotRule)
                    .where(BotRule.is_active == True)  # noqa: E712
                    .order_by(BotRule.sort_order, BotRule.id)
                )
            ).scalars().all()
            labels = await load_labels(session)
    except Exception:
        logger.exception("SubWorld rules read failed")
        await callback.message.edit_text(
            "القوانين غير متاحة حاليا." if lang == "ar" else "Rules are unavailable right now.",
            reply_markup=_rules_back_kb(lang, labels),
        )
        await callback.answer()
        return
    if not rows:
        text = (
            "لا توجد قوانين بعد — أضفها من لوحة التحكم."
            if lang == "ar"
            else "No rules yet — add them from the admin panel."
        )
    else:
        text = "\n".join(f"{i}. {r.text_for(lang)}" for i, r in enumerate(rows, 1))
    await callback.message.edit_text(text, reply_markup=_rules_back_kb(lang, labels))
    await callback.answer()


def _rules_back_kb(lang: str, labels: dict) -> types.InlineKeyboardMarkup:
    text, style = label_text(labels, "main_menu", lang)
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=text, callback_data="menu:main", style=style)]
        ]
    )

