"""One-time seeding of the 23 bilingual bot rules for the Yemen bot.

The rules are the 23 adapted rules (built from the Yemen bot's own 18),
with three wording fixes for the merged bot:
  1. Rule 1 names the actual bot (Yemen Platform, not SubWorld).
  2. Rule 5: both payment worlds (Yemeni wallets + Binance/crypto/wallet).
  3. Rule 22: prices are based on USD and also shown in SAR and YER
     (the SubWorld text said USD only, which is wrong for this bot).
Rule 4 is unchanged from the adapted text: delivery is through the
Telegram account or the phone number - there is NO email invite in the
bot or the store; email belongs to the store's website only.

REPLACE mode, guarded by a sentinel: if rule 1's Arabic text is already
in the table, nothing is touched, so rules the owner adds later from the
panel are safe. The function also adds the text_en column if it is
missing (the model patch adds it too; the raw SQL here works even
before the model is reloaded).
"""

YEMEN_RULES: list[tuple[str, str]] = [
    (
        "منصة اليمن الإلكترونية وسيط لبيع الاشتراكات الرقمية، وهي جهة مستقلة وغير تابعة لـ Google أو OpenAI أو أي مزوّد آخر، وجميع العلامات التجارية ملك لأصحابها.",
        "Yemen Platform is an independent reseller of digital subscriptions and is not affiliated with Google, OpenAI, or any other provider. All trademarks belong to their respective owners.",
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
        "الأسعار المعلنة وقت الطلب هي المعتمدة، والدفع مقدم عبر طرق الدفع المتاحة في البوت (المحافظ اليمنية: الكريمي وجيب وموبايل موني، وبينانس، والعملات الرقمية، والمحفظة)، ولا يبدأ تنفيذ الطلب قبل تأكيد الدفع.",
        "The prices shown at the time of ordering are the applicable ones. Payment is made in advance through the payment methods available in the bot (Yemeni wallets: Kuraimi, Jeeb, and Mobile Money, Binance, cryptocurrency, and wallet), and your order is not processed until payment is confirmed.",
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
        "الأسعار بالدولار الأمريكي أساسا، وتظهر أيضا بالريال السعودي والريال اليمني حسب سعر الصرف.",
        "Prices are based on US dollars, and are also shown in Saudi riyals and Yemeni riyals according to the exchange rate.",
    ),
    (
        "لا يستطيع أي عميل استرداد المبالغ نقدا في حال حصل أي خلل؛ بل تُضاف المبالغ إلى رصيد محفظته في البوت ويستطيع شراء أي منتج آخر بها.",
        "No customer can receive a cash refund if any problem occurs. Instead, the amount is added to their wallet balance in the bot, and they can use it to buy any other product.",
    ),
]

RULE_1_AR = YEMEN_RULES[0][0]


async def ensure_yemen_rules_seeded() -> None:
    """Seed the 23 bilingual rules once (REPLACE mode, sentinel-guarded).

    Adds the text_en column if missing, then replaces the whole bot_rules
    table with the 23 rules above - but only if rule 1's Arabic text is
    not already there, so a later run (or rules the owner adds from the
    panel) are never touched.
    """
    from bot.database.main import Database as _DB
    from sqlalchemy import text as _sql_text

    async with _DB().session() as session:
        await session.execute(
            _sql_text("ALTER TABLE bot_rules ADD COLUMN IF NOT EXISTS text_en TEXT")
        )
        found = await session.execute(
            _sql_text("SELECT 1 FROM bot_rules WHERE text = :t LIMIT 1"),
            {"t": RULE_1_AR},
        )
        if found.scalar() is not None:
            await session.commit()
            return
        await session.execute(_sql_text("DELETE FROM bot_rules"))
        for i, (ar, en) in enumerate(YEMEN_RULES, start=1):
            await session.execute(
                _sql_text(
                    "INSERT INTO bot_rules (text, text_en, sort_order, is_active) "
                    "VALUES (:t, :e, :s, TRUE)"
                ),
                {"t": ar, "e": en, "s": i},
            )
        await session.commit()
