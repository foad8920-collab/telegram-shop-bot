# Apply-Yemen-TxID-Patch-v4.ps1
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-TxID-Patch-v4.ps1
# PowerShell 5.1 mis-reads the Arabic in script files, so anchors with
# Arabic never match there (that was the v1/v2 failure). pwsh reads UTF-8.
#
# What v4 does (purchase-flow TxID, on top of the v2 top-up work):
#   1. Payment-method order: Binance first, Crypto (USDT TRC20) second,
#      Yemeni wallets after, in every method list (top-up + local buy x2
#      + supplier buy x2).
#   2. Copy fix: only the Pay ID / TRC20 address sits in the <code> block,
#      the rest of the account info is plain text - so tapping it copies
#      only the ID, not the whole text.
#   3. No photo for Binance/Crypto in purchases: the display asks for the
#      TxID, and a pasted TxID is verified (Binance Pay / TronGrid) and
#      completes the purchase automatically. Wallets keep the photo flow.
#   4. Local buy: the Binance branch already asked for a code but NO
#      handler ever listened (dead end) - v4 adds the handler, and routes
#      Crypto through it too.
#
# Files touched:
#   bot/handlers/user/balance_and_payment.py - import + order + copy split
#   bot/handlers/user/local_buy.py           - imports + order + Binance/Crypto
#                                              branch + TxID handler
#   bot/handlers/user/supplier_buy.py        - imports + order + TxID display
#                                              branch + TxID handler
# The extended bot/misc/services/txid_verify.py (new helpers) comes from
# the zip itself. The VerifiedTransaction model (v1) is already in place.
#
# Safe: every edit is anchor-checked (exact count) before any write, the
# script is idempotent - re-running it skips edits already applied - and
# it never calls exit; on a failed anchor it reports the problem and
# returns without writing that file.

$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Read-Text([string]$Path) {
    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $crlf = $raw.Contains("`r`n")
    $content = $raw -replace "`r`n", "`n"
    return @{ Text = $content; Crlf = $crlf }
}

function Write-Text([string]$Path, [string]$Content, [bool]$Crlf) {
    if ($Crlf) { $Content = $Content -replace "`n", "`r`n" }
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Count-Anchor([string]$Content, [string]$Anchor) {
    return ([regex]::Matches($Content, [regex]::Escape($Anchor))).Count
}

function Test-Utf8([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $mark = [byte[]](0xD8, 0xB5, 0xD9, 0x88, 0xD8, 0xB1, 0xD8, 0xA9)
    for ($i = 0; $i -le ($bytes.Length - $mark.Length); $i++) {
        $match = $true
        for ($j = 0; $j -lt $mark.Length; $j++) {
            if ($bytes[$i + $j] -ne $mark[$j]) { $match = $false; break }
        }
        if ($match) { return $true }
    }
    return $false
}

# ---------------- 0) backup before any write ----------------
$backupDir = "backup-2026-11-28-txid"
if (Test-Path $backupDir) { $backupDir = "backup-2026-11-28-txid-v2" }
if (Test-Path $backupDir) { $backupDir = "backup-2026-11-28-txid-v3" }
if (Test-Path $backupDir) { $backupDir = "backup-2026-11-28-txid-v4" }
$backupFiles = @("bot\handlers\user\balance_and_payment.py", "bot\handlers\user\local_buy.py", "bot\handlers\user\supplier_buy.py")
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $backupFiles) {
    if (-not (Test-Path $f)) { Write-Host "MISSING FILE: $f"; return }
    Copy-Item $f "$backupDir\" -Force
}
Write-Host "Backup saved in $backupDir."
Write-Host "To restore: Copy-Item $backupDir\balance_and_payment.py bot\handlers\user\ -Force; Copy-Item $backupDir\local_buy.py bot\handlers\user\ -Force; Copy-Item $backupDir\supplier_buy.py bot\handlers\user\ -Force; git add bot; git commit -m 'restore before TxID patch v4'; git push"

# Guards: the new txid_verify.py (with the helpers) must be in place, and
# all three patched files must be UTF-8.
if (-not (Test-Path "bot\misc\services\txid_verify.py")) {
    Write-Host "MISSING: bot\misc\services\txid_verify.py - extract the zip into the project root first."
    return
}
$txv = [System.IO.File]::ReadAllText("bot\misc\services\txid_verify.py", [System.Text.Encoding]::UTF8)
if (-not $txv.Contains("def copyable_account_value")) {
    Write-Host "STOP: txid_verify.py has no copyable_account_value - the v4 zip was not extracted (old file still there). Extract the zip with -Force, then re-run."
    return
}
foreach ($f in $backupFiles) {
    if (-not (Test-Utf8 $f)) {
        Write-Host "STOP: $f is not UTF-8 (Arabic bytes not found). Re-save it as UTF-8 in your editor, then re-run. Nothing was changed."
        return
    }
}

# ---------------- 1) balance_and_payment.py (top-up) ----------------
$payPath = "bot\handlers\user\balance_and_payment.py"
$pay = Read-Text $payPath
$payText = $pay.Text

# 1a) extend the txid_verify import with the new helpers
if ($payText.Contains("copyable_account_value")) {
    Write-Host "payment: txid helpers import already present - skipped."
} else {
    $pOld = "from bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid"
    $pNew = "from bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid, copyable_account_value, account_info_rest_lines, payment_method_order_key"
    if ((Count-Anchor $payText $pOld) -ne 1) {
        Write-Host "ANCHOR FAILED: payment txid import (found $((Count-Anchor $payText $pOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($pOld, $pNew)
    Write-Host "payment: txid helpers import added."
}

# 1b) order: Binance first, Crypto second in the top-up method list
if ($payText.Contains("methods = sorted(methods, key=payment_method_order_key)")) {
    Write-Host "payment: method order already present - skipped."
} else {
    $oOld = @'
        methods = (await session.execute(
            select(PaymentMethod).where(PaymentMethod.is_active == True)
        )).scalars().all()
'@
    $oNew = @'
        methods = (await session.execute(
            select(PaymentMethod).where(PaymentMethod.is_active == True)
        )).scalars().all()
    methods = sorted(methods, key=payment_method_order_key)
'@
    if ((Count-Anchor $payText $oOld) -ne 1) {
        Write-Host "ANCHOR FAILED: payment method fetch (found $((Count-Anchor $payText $oOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($oOld, $oNew)
    Write-Host "payment: method order added."
}

# 1c) copy split: only the Pay ID / address in the code block
if ($payText.Contains("_copy_value = copyable_account_value")) {
    Write-Host "payment: copy split already present - skipped."
} else {
    $cOld = @'
    _txid_kind = classify_txid_method(pm.name or "")
    if _txid_kind:
        _instruction = " قم بتحويل المبلغ، ثم أرسل <b>ايدي العملية (TxID)</b> هنا - انسخه من تطبيق بينانس/محفظتك - أو أرسل <b>صورة السند</b>."
    else:
        _instruction = " قم بتحويل المبلغ، ثم أرسل <b>صورة سند الحوالة</b> هنا."

    _entered_txt, _usd_txt = _topup_display(data)
    await call.message.edit_text(
        f" <b>{pm.name}</b>\n\n"
        f" المبلغ: <b>{_entered_txt}</b>\n\n"
        f" <b>رقم الحساب / التعليمات:</b>\n"
        f"<code>{pm.account_info}</code>\n\n"
        f" {_instruction}",
        parse_mode="HTML"
    )
'@
    $cNew = @'
    _txid_kind = classify_txid_method(pm.name or "")
    if _txid_kind:
        _instruction = " قم بتحويل المبلغ، ثم أرسل <b>ايدي العملية (TxID)</b> هنا - انسخه من تطبيق بينانس/محفظتك - أو أرسل <b>صورة السند</b>."
    else:
        _instruction = " قم بتحويل المبلغ، ثم أرسل <b>صورة سند الحوالة</b> هنا."

    _copy_value = copyable_account_value(pm.name, pm.account_info)
    if _copy_value:
        _rest = account_info_rest_lines(pm.account_info, _copy_value)
        _account_block = f"<code>{_copy_value}</code>" + ("\n" + "\n".join(_rest) if _rest else "")
    else:
        _account_block = f"<code>{pm.account_info}</code>"

    _entered_txt, _usd_txt = _topup_display(data)
    await call.message.edit_text(
        f" <b>{pm.name}</b>\n\n"
        f" المبلغ: <b>{_entered_txt}</b>\n\n"
        f" <b>رقم الحساب / التعليمات:</b>\n"
        f"{_account_block}\n\n"
        f" {_instruction}",
        parse_mode="HTML"
    )
'@
    if ((Count-Anchor $payText $cOld) -ne 1) {
        Write-Host "ANCHOR FAILED: payment show_account (found $((Count-Anchor $payText $cOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($cOld, $cNew)
    Write-Host "payment: copy split added to show_account."
}

Write-Text $payPath $payText $pay.Crlf
Write-Host "payment: written."

# ---------------- 2) local_buy.py ----------------
$lbPath = "bot\handlers\user\local_buy.py"
$lb = Read-Text $lbPath
$lbText = $lb.Text

# 2a) imports (one block: Message + IntegrityError + VerifiedTransaction + txid helpers)
if ($lbText.Contains("from bot.misc.services.txid_verify import")) {
    Write-Host "local: txid imports already present - skipped."
} else {
    $iOld = @'
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
'@
    $iNew = @'
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import noload

from bot.database.main import Database
from bot.database.models.main import (
    LocalSubscription, PaymentMethod, User,
    LocalSubscriptionOrder, LocalSubscriptionCode, VerifiedTransaction,
)
from bot.states import LocalBuy
from bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid, copyable_account_value, account_info_rest_lines, payment_method_order_key, TxVerifyError
'@
    if ((Count-Anchor $lbText $iOld) -ne 1) {
        Write-Host "ANCHOR FAILED: local imports (found $((Count-Anchor $lbText $iOld)), need 1). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($iOld, $iNew)
    Write-Host "local: imports added."
}

# 2b) order: Binance first, Crypto second (both method-list sites)
if ($lbText.Contains("methods = sorted(methods, key=payment_method_order_key)")) {
    Write-Host "local: method order already present - skipped."
} else {
    $oOld = "        methods = result.scalars().all()"
    $oNew = "        methods = result.scalars().all()`n        methods = sorted(methods, key=payment_method_order_key)"
    if ((Count-Anchor $lbText $oOld) -ne 2) {
        Write-Host "ANCHOR FAILED: local method fetch (found $((Count-Anchor $lbText $oOld)), need 2). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($oOld, $oNew)
    Write-Host "local: method order added (2 sites)."
}

# 2c) Binance/Crypto branch: classify + copy split + TxID wording
if ($lbText.Contains("is_verifiable = _txid_kind is not None")) {
    Write-Host "local: verifiable branch already present - skipped."
} else {
    $bOld = @'
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
'@
    $bNew = @'
    _txid_kind = classify_txid_method(pm.name or "")
    if _txid_kind is None and pm.id == 1:
        _txid_kind = "binance_pay"
    is_verifiable = _txid_kind is not None

    await state.update_data(payment_method_id=pm.id, payment_method_name=pm.name)

    total = Decimal(str(data.get("total_price", "0"))).quantize(Decimal("0.01"))

    if is_verifiable:
        # الطريقة الآلية: التحقق من ايدي العملية (TxID) عبر API
        await state.set_state(LocalBuy.waiting_binance_code)
        _copy_value = copyable_account_value(pm.name, pm.account_info)
        _account_lines = []
        if _copy_value:
            _account_lines.append(f"<code>{_copy_value}</code>")
            _account_lines.extend(account_info_rest_lines(pm.account_info, _copy_value))
        else:
            _account_lines.append(f"<code>{pm.account_info}</code>")
        _hint = "ايدي عملية بينانس يبدأ بـ M_P_." if _txid_kind == "binance_pay" else "ايدي عملية ترون مكوّن من 64 حرفا ورقما."
        text = NL.join([
            f" <b>الدفع عبر {pm.name}</b>",
            "",
            f" المبلغ المطلوب: <b>{_fmt(total)}</b>",
            "",
            " <b>بيانات الحساب:</b>",
            *_account_lines,
            "",
            "",
            " حوّل المبلغ إلى الحساب أعلاه.",
            " ثم أرسل <b>ايدي العملية (TxID)</b> هنا — انسخه من تطبيق بينانس/محفظتك.",
            "",
            f" <i>{_hint} أرسل الايدي فقط بدون أي نص إضافي.</i>",
        ])
'@
    if ((Count-Anchor $lbText $bOld) -ne 1) {
        Write-Host "ANCHOR FAILED: local Binance branch (found $((Count-Anchor $lbText $bOld)), need 1). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($bOld, $bNew)
    Write-Host "local: verifiable branch added."
}

# 2d) TxID handler for waiting_binance_code (the state had no handler at all)
if ($lbText.Contains("async def binance_code_receive")) {
    Write-Host "local: TxID handler already present - skipped."
} else {
    $hAnchor = @'
# ============================================================
#  Manual quantity input
# ============================================================
'@
    $hNew = @'
@router.message(LocalBuy.waiting_binance_code, F.text)
async def binance_code_receive(message: Message, state: FSMContext) -> None:
    """Verify a Binance Pay / USDT TRC20 TxID and complete the local purchase."""
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return
    data = await state.get_data()
    sub_id = data.get("sub_id")
    qty = data.get("qty")
    total = data.get("total_price")
    pm_id = data.get("payment_method_id")
    pm_name = data.get("payment_method_name", "")
    if not sub_id or not qty or not total or not pm_id:
        await message.answer(" انتهت الجلسة. أعد العملية من البداية.")
        await state.clear()
        return
    qty = int(qty)
    sub_id = int(sub_id)
    pm_id = int(pm_id)
    kind = classify_txid_method(pm_name or "")
    if kind is None and pm_id == 1:
        kind = "binance_pay"
    if kind is None:
        await message.answer(" هذه الطريقة لا تدعم التحقق التلقائي. أرسل صورة السند.")
        return
    txid, hint = validate_txid_format(raw, kind)
    if txid is None:
        await message.answer(
            f" رقم العملية غير صحيح. {hint}\n\nأرسل الايدي فقط بدون أي نص إضافي.",
            parse_mode="HTML",
        )
        return
    total_dec = Decimal(str(total)).quantize(Decimal("0.01"))
    unit_price = Decimal(str(data.get("sub_price", "0")))
    user_id = message.from_user.id
    try:
        async with Database().session() as session:
            await session.run_sync(
                lambda sync_session: VerifiedTransaction.__table__.create(sync_session.bind, checkfirst=True)
            )
            exists = (await session.execute(
                select(VerifiedTransaction.id).where(VerifiedTransaction.txid == txid)
            )).scalar_one_or_none()
            if exists is not None:
                await message.answer(" هذا الايدي مستخدم من قبل. تأكد من الرقم أو أرسل ايدي عملية جديدة.")
                return
    except Exception as e:
        logger.exception("TxID table check failed: %s", e)
        await message.answer(" حدث خطأ. حاول لاحقاً.")
        return
    try:
        if kind == "binance_pay":
            ok, received, reason = await verify_binance_pay_txid(txid, total_dec)
        else:
            ok, received, reason = await verify_tron_usdt_txid(txid, total_dec)
    except TxVerifyError as e:
        await message.answer(f" تعذر التحقق الآن: {e}\n\nحاول لاحقاً أو أرسل صورة السند للمشرف.")
        return
    if not ok:
        reasons = {
            "not_found": " لم نجد هذه العملية. تأكد من الرقم وأنه لنفس المبلغ.",
            "wrong_receiver": " هذه العملية لم تصل إلى حسابنا. تأكد من رقم الحساب.",
            "amount_low": " المبلغ المحوّل أقل من المطلوب.",
            "wrong_type": " هذه العملية ليست تحويلا مباشرا.",
            "tx_failed": " هذه العملية فاشلة على الشبكة.",
            "not_configured": " التحقق التلقائي غير مهيأ بعد. أرسل صورة السند وسيؤكدها المشرف.",
        }
        await message.answer(
            (reasons.get(reason) or " تعذر التحقق من العملية.")
            + "\n\nأرسل ايدي عملية أخرى أو صورة السند.",
            parse_mode="HTML",
        )
        return
    try:
        async with Database().session() as session:
            session.add(VerifiedTransaction(
                txid=txid, method=kind, user_id=user_id,
                amount_usd=Decimal(str(received)).quantize(Decimal("0.01")),
                used_for="local_buy",
            ))
            await session.commit()
    except IntegrityError:
        await message.answer(" هذا الايدي مستخدم من قبل. تأكد من الرقم أو أرسل ايدي عملية جديدة.")
        return
    except Exception as e:
        logger.exception("TxID record failed: %s", e)
        await message.answer(" حدث خطأ. حاول لاحقاً.")
        return
    payment_type = "binance" if kind == "binance_pay" else "crypto"
    try:
        async with Database().session() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )).scalar_one_or_none()
            if user is None:
                await message.answer("تعذّر العثور على حسابك.")
                await state.clear()
                return
            sub = (await session.execute(
                select(LocalSubscription).where(LocalSubscription.id == sub_id).with_for_update()
            )).scalar_one_or_none()
            if sub is None or not sub.is_active:
                await message.answer("هذا الاشتراك لم يعد متوفرًا.")
                await state.clear()
                return
            codes_per_purchase = int(sub.codes_per_purchase or 1)
            if codes_per_purchase < 1:
                codes_per_purchase = 1
            delivery_type = (sub.delivery_type or "codes").lower()
            required_codes = 0
            order_status = "completed"
            if delivery_type == "codes":
                required_codes = qty * codes_per_purchase
                free_count = (await session.execute(
                    select(func.count()).select_from(LocalSubscriptionCode).where(
                        LocalSubscriptionCode.subscription_id == sub_id,
                        LocalSubscriptionCode.is_used == False,
                    )
                )).scalar() or 0
                if int(free_count) < required_codes:
                    order_status = "pending"
            else:
                if int(sub.quantity or 0) < qty:
                    order_status = "pending"
            codes = []
            if order_status == "completed" and required_codes > 0:
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
                    order_status = "pending"
                    codes = []
            if order_status == "completed" and delivery_type != "codes":
                sub.quantity = int(sub.quantity or 0) - qty
            order = LocalSubscriptionOrder(
                user_id=user_id, subscription_id=sub_id, quantity=qty,
                unit_price=unit_price, total_price=total_dec,
                payment_method_id=pm_id, payment_type=payment_type,
                payment_reference=txid, status=order_status,
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
            sub_name = str(sub.name)
            if codes_content:
                try:
                    await send_purchase_post(message.bot, sub_name, qty, _fmt(total_dec), pm_name, user_id, order_id)
                except Exception as _e:
                    logger.warning("Failed to post purchase: %s", _e)
    except Exception as e:
        logger.exception("TxID purchase completion failed: %s", e)
        await message.answer(" تم التحقق من العملية، لكن حدث خطأ أثناء إتمام الشراء. تواصل مع المشرف (رقم العملية محفوظ).")
        await state.clear()
        return
    await state.clear()
    if order_status == "pending":
        await message.answer(
            NL.join([
                " <b>تم التحقق من العملية بنجاح!</b>",
                "",
                f" المنتج: <b>{sub_name}</b>",
                f" الكمية: <b>{qty}</b>",
                f" الإجمالي: <b>{_fmt(total_dec)}</b>",
                f" رقم الطلب: <code>#{order_id}</code>",
                "",
                " <i>المنتج غير متوفر حاليا — تم تسجيل طلبك وسيتواصل معك المشرف.</i>",
            ]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
            ]),
        )
        return
    if codes_content:
        lines = [
            " <b>تم إتمام الشراء بنجاح!</b>",
            "",
            f" المنتج: <b>{sub_name}</b>",
            f" الكمية: <b>{qty}</b>",
            f" الإجمالي: <b>{_fmt(total_dec)}</b>",
            f" رقم الطلب: <code>#{order_id}</code>",
            "",
            " <b>الأكواد / المفاتيح الخاصة بك:</b>",
            "",
        ]
        for idx, c in enumerate(codes_content, 1):
            lines.append(f"<b>{idx}.</b>")
            for _line in str(c).splitlines():
                _line = _line.strip()
                if not _line:
                    continue
                if ": " in _line:
                    _label, _value = _line.split(": ", 1)
                    _value = _value.strip()
                    if _value:
                        lines.append(f"{html.escape(_label.strip(), quote=False)}: <code>{html.escape(_value, quote=False)}</code>")
                    else:
                        lines.append(f"<code>{html.escape(_line, quote=False)}</code>")
                else:
                    lines.append(f"<code>{html.escape(_line, quote=False)}</code>")
        lines.append("")
        lines.append(" <i>احتفظ بهذه الأكواد  لن تُعرض مرة أخرى.</i>")
        await message.answer(
            NL.join(lines), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
            ]),
        )
        return
    invite_field = "email"
    try:
        async with Database().session() as _sess3:
            _sub3 = (await _sess3.execute(
                select(LocalSubscription).where(LocalSubscription.id == sub_id)
            )).scalar_one_or_none()
            if _sub3 is not None:
                invite_field = (_sub3.invite_field_type or "email").lower()
    except Exception as e:
        logger.warning("Failed to reload sub in txid: %s", e)
    if delivery_type == "invite" and invite_field != "none":
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
            f" الإجمالي: <b>{_fmt(total_dec)}</b>",
            f" رقم الطلب: <code>#{order_id}</code>",
            "",
            f"{field_label}:",
            "<i>مثال: user@gmail.com</i>",
            "",
            " <i>جاري تفعيل اشتراكك من قبل المشرف  يرجى الانتظار.</i>",
        ])
        await message.answer(
            prompt_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
            ]),
        )
    else:
        manual_text = NL.join([
            " <b>تم إتمام الشراء بنجاح!</b>",
            "",
            f" المنتج: <b>{sub_name}</b>",
            f" الكمية: <b>{qty}</b>",
            f" الإجمالي: <b>{_fmt(total_dec)}</b>",
            f" رقم الطلب: <code>#{order_id}</code>",
            "",
            " <i>جاري تفعيل اشتراكك من قبل المشرف  يرجى الانتظار.</i>",
        ])
        await message.answer(
            manual_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
            ]),
        )


'@
    if ((Count-Anchor $lbText $hAnchor) -ne 1) {
        Write-Host "ANCHOR FAILED: local manual-quantity header (found $((Count-Anchor $lbText $hAnchor)), need 1). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($hAnchor, $hNew + $hAnchor)
    Write-Host "local: TxID handler inserted."
}

Write-Text $lbPath $lbText $lb.Crlf
Write-Host "local: written."

# ---------------- 3) supplier_buy.py ----------------
$sbPath = "bot\handlers\user\supplier_buy.py"
$sb = Read-Text $sbPath
$sbText = $sb.Text

# 3a) imports
if ($sbText.Contains("from bot.misc.services.txid_verify import")) {
    Write-Host "supplier: txid imports already present - skipped."
} else {
    $sOld1 = "from sqlalchemy import select"
    $sNew1 = "from sqlalchemy import select`nfrom sqlalchemy.exc import IntegrityError"
    if ((Count-Anchor $sbText $sOld1) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier sqlalchemy import (found $((Count-Anchor $sbText $sOld1)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($sOld1, $sNew1)
    $sOld2 = "    SupplierOrder, User,"
    $sNew2 = "    SupplierOrder, User, VerifiedTransaction,"
    if ((Count-Anchor $sbText $sOld2) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier models import (found $((Count-Anchor $sbText $sOld2)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($sOld2, $sNew2)
    $sOld3 = "from bot.states import SupplierBuy"
    $sNew3 = "from bot.states import SupplierBuy`nfrom bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid, copyable_account_value, account_info_rest_lines, payment_method_order_key, TxVerifyError"
    if ((Count-Anchor $sbText $sOld3) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier states import (found $((Count-Anchor $sbText $sOld3)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($sOld3, $sNew3)
    Write-Host "supplier: imports added."
}

# 3b) order: Binance first, Crypto second (both method-list sites)
if ($sbText.Contains("methods = sorted(methods, key=payment_method_order_key)")) {
    Write-Host "supplier: method order already present - skipped."
} else {
    $oOld = "        methods = list(r.scalars().all())"
    $oNew = "        methods = list(r.scalars().all())`n        methods = sorted(methods, key=payment_method_order_key)"
    if ((Count-Anchor $sbText $oOld) -ne 2) {
        Write-Host "ANCHOR FAILED: supplier method fetch (found $((Count-Anchor $sbText $oOld)), need 2). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($oOld, $oNew)
    Write-Host "supplier: method order added (2 sites)."
}

# 3c) display computation: classify + copy split + steps
if ($sbText.Contains("_account_lines = []")) {
    Write-Host "supplier: display computation already present - skipped."
} else {
    $dOld = "    await state.set_state(SupplierBuy.waiting_wallet_proof)"
    $dNew = @'
    await state.set_state(SupplierBuy.waiting_wallet_proof)
    _txid_kind = classify_txid_method(pm.name or "")
    _copy_value = copyable_account_value(pm.name, pm.account_info)
    _account_lines = []
    if _copy_value:
        _account_lines.append(f"<code>{_copy_value}</code>")
        _account_lines.extend(account_info_rest_lines(pm.account_info, _copy_value))
    else:
        _account_lines.append(f"<code>{pm.account_info}</code>")
    if _txid_kind:
        _steps = [
            "1) حوّل المبلغ للحساب اعلاه.",
            "2) أرسل <b>ايدي العملية (TxID)</b> هنا — انسخه من تطبيق بينانس/محفظتك.",
            "",
            " <i>أرسل الايدي فقط بدون أي نص إضافي. سيتم التحقق تلقائيا.</i>",
        ]
    else:
        _steps = [
            "1) حوّل المبلغ للحساب اعلاه.",
            "2) ارسل <b>صورة سند الحوالة</b> هنا.",
            "",
            "سيتم التحقق بواسطة الادارة.",
        ]
'@
    if ((Count-Anchor $sbText $dOld) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier set_state (found $((Count-Anchor $sbText $dOld)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($dOld, $dNew)
    Write-Host "supplier: display computation added."
}

# 3d) display text: use the split account lines + the right steps
if ($sbText.Contains("*_account_lines,")) {
    Write-Host "supplier: display text already present - skipped."
} else {
    $tOld = @'
    text = NL.join([
        f"<b>{pm.name}</b>",
        "====================",
        f"المبلغ المطلوب: <b>{_fmt(total)}</b>",
        "",
        "<b>بيانات الحساب:</b>",
        f"<code>{pm.account_info}</code>",
        "",
        "====================",
        "1) حوّل المبلغ للحساب اعلاه.",
        "2) ارسل <b>صورة سند الحوالة</b> هنا.",
        "",
        "سيتم التحقق بواسطة الادارة.",
    ])
'@
    $tNew = @'
    text = NL.join([
        f"<b>{pm.name}</b>",
        "====================",
        f"المبلغ المطلوب: <b>{_fmt(total)}</b>",
        "",
        "<b>بيانات الحساب:</b>",
        *_account_lines,
        "",
        "====================",
        *_steps,
    ])
'@
    if ((Count-Anchor $sbText $tOld) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier display text (found $((Count-Anchor $sbText $tOld)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($tOld, $tNew)
    Write-Host "supplier: display text updated."
}

# 3e) TxID handler for waiting_wallet_proof (before the wrong-type catch-all)
if ($sbText.Contains("async def supplier_txid_handler")) {
    Write-Host "supplier: TxID handler already present - skipped."
} else {
    $wAnchor = @'
@router.message(SupplierBuy.waiting_wallet_proof)
async def supplier_wallet_proof_wrong_type(message: Message, state: FSMContext) -> None:
'@
    $wNew = @'
@router.message(SupplierBuy.waiting_wallet_proof, F.text)
async def supplier_txid_handler(message: Message, state: FSMContext) -> None:
    """Verify a Binance Pay / USDT TRC20 TxID and create the supplier order."""
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return
    data = await state.get_data()
    supplier_id = data.get("sb_supplier_id")
    product_id = data.get("sb_product_id")
    qty = data.get("sb_qty")
    payment_method_id = data.get("sb_payment_method_id")
    payment_method_name = data.get("sb_payment_method_name") or "-"
    if not supplier_id or not product_id or not qty or not payment_method_id:
        await message.answer(" انتهت الجلسة. أعد العملية من البداية.")
        await state.clear()
        return
    kind = classify_txid_method(payment_method_name or "")
    if kind is None:
        await message.answer(" الرجاء إرسال <b>صورة سند الحوالة</b> (وليس نصا).", parse_mode="HTML")
        return
    txid, hint = validate_txid_format(raw, kind)
    if txid is None:
        await message.answer(
            f" رقم العملية غير صحيح. {hint}\n\nأرسل الايدي فقط بدون أي نص إضافي.",
            parse_mode="HTML",
        )
        return
    unit_price = Decimal(str(data.get("sb_unit_price", "0")))
    total = (unit_price * int(qty)).quantize(Decimal("0.01"))
    try:
        async with Database().session() as session:
            await session.run_sync(
                lambda sync_session: VerifiedTransaction.__table__.create(sync_session.bind, checkfirst=True)
            )
            exists = (await session.execute(
                select(VerifiedTransaction.id).where(VerifiedTransaction.txid == txid)
            )).scalar_one_or_none()
            if exists is not None:
                await message.answer(" هذا الايدي مستخدم من قبل. تأكد من الرقم أو أرسل ايدي عملية جديدة.")
                return
    except Exception as e:
        logger.exception("TxID table check failed: %s", e)
        await message.answer(" حدث خطأ. حاول لاحقاً.")
        return
    try:
        if kind == "binance_pay":
            ok, received, reason = await verify_binance_pay_txid(txid, total)
        else:
            ok, received, reason = await verify_tron_usdt_txid(txid, total)
    except TxVerifyError as e:
        await message.answer(f" تعذر التحقق الآن: {e}\n\nحاول لاحقاً أو أرسل صورة السند للمشرف.")
        return
    if not ok:
        reasons = {
            "not_found": " لم نجد هذه العملية. تأكد من الرقم وأنه لنفس المبلغ.",
            "wrong_receiver": " هذه العملية لم تصل إلى حسابنا. تأكد من رقم الحساب.",
            "amount_low": " المبلغ المحوّل أقل من المطلوب.",
            "wrong_type": " هذه العملية ليست تحويلا مباشرا.",
            "tx_failed": " هذه العملية فاشلة على الشبكة.",
            "not_configured": " التحقق التلقائي غير مهيأ بعد. أرسل صورة السند وسيؤكدها المشرف.",
        }
        await message.answer(
            (reasons.get(reason) or " تعذر التحقق من العملية.")
            + "\n\nأرسل ايدي عملية أخرى أو صورة السند.",
            parse_mode="HTML",
        )
        return
    try:
        async with Database().session() as session:
            session.add(VerifiedTransaction(
                txid=txid, method=kind, user_id=message.from_user.id,
                amount_usd=Decimal(str(received)).quantize(Decimal("0.01")),
                used_for="supplier_buy",
            ))
            await session.commit()
    except IntegrityError:
        await message.answer(" هذا الايدي مستخدم من قبل. تأكد من الرقم أو أرسل ايدي عملية جديدة.")
        return
    except Exception as e:
        logger.exception("TxID record failed: %s", e)
        await message.answer(" حدث خطأ. حاول لاحقاً.")
        return
    user_id = message.from_user.id
    try:
        async with Database().session() as session:
            product = await session.get(SupplierProduct, int(product_id))
            if product is None or int(product.supplier_id) != int(supplier_id):
                await message.answer(" بيانات غير صحيحة.")
                await state.clear()
                return
            supplier = await session.get(Supplier, int(supplier_id))
            unit_price = Decimal(str(product.selling_price or 0)).quantize(Decimal("0.01"))
            total = (unit_price * int(qty)).quantize(Decimal("0.01"))
            order = SupplierOrder(
                supplier_id=int(supplier_id),
                user_id=user_id,
                product_id=int(product_id),
                quantity=int(qty),
                cost_price=product.cost_price,
                selling_price=unit_price,
                profit=(unit_price - Decimal(str(product.cost_price or 0))) * int(qty),
                status="pending",
                response_data={
                    "payment_method": "txid",
                    "payment_method_id": int(payment_method_id),
                    "payment_method_name": str(payment_method_name),
                    "txid": txid,
                    "verified_amount": str(received),
                    "total": str(total),
                },
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)
            order_id = order.id
            product_name = str(product.name)
            product_external_id = str(getattr(product, "external_id", "") or "")
            product_in_local = bool(getattr(product, "show_in_local", False))
            supplier_name = (supplier.display_name or supplier.name) if supplier else "-"
            supplier_internal_name = supplier.name if supplier else "-"
            supplier_icon = supplier.icon_emoji if supplier else ""
    except Exception as e:
        logger.exception("Failed to create supplier TxID order: %s", e)
        await message.answer(" تم التحقق من العملية، لكن حدث خطأ أثناء إنشاء الطلب. تواصل مع المشرف (رقم العملية محفوظ).")
        await state.clear()
        return
    if product_in_local:
        supplier_note = "<b>ملاحظة: هذا المنتج منقول الى الاشتراكات المحلية لذلك لن يظهر في قسم الموردين في البوت. اشتره من بوت المورد في تيليجرام كمستخدم عادي ثم ارسل الاشتراك للعميل من هنا.</b>"
    else:
        supplier_note = "<b>اشتر المنتج من بوت المورد في تيليجرام كمستخدم عادي ثم ارسل الاشتراك للعميل من هنا.</b>"
    admin_text = NL.join([
        f"{supplier_icon} <b>طلب شراء جديد من {supplier_name}</b>",
        "====================",
        f"رقم الطلب: <code>#{order_id}</code>",
        f"العميل: <code>{user_id}</code>",
        f"المنتج: <b>{product_name}</b>",
        f"المورد: <b>{supplier_name}</b>",
        f"اسم المورد الداخلي: <b>{supplier_internal_name}</b> (ID: {supplier_id})",
        f"المعرف الخارجي: <code>{product_external_id or '-'}</code>",
        f"الكمية: <b>{qty}</b>",
        f"الاجمالي: <b>{_fmt(total)}</b>",
        f"طريقة الدفع: <b>{payment_method_name}</b> (تحقق تلقائي - TxID: <code>{txid}</code>)",
        "====================",
        supplier_note,
    ])
    SUPPLIER_TELEGRAM_BOTS = {1: "stackvault_bot", 2: "Pandora_Digital_bot", 6: "storeBatmanBot"}
    supplier_telegram_bot = SUPPLIER_TELEGRAM_BOTS.get(int(supplier_id), "")
    admin_kb_rows: list[list[InlineKeyboardButton]] = []
    if supplier_telegram_bot:
        admin_kb_rows.append([InlineKeyboardButton(text=f"فتح بوت {supplier_internal_name} في تيليجرام", url=f"https://t.me/{supplier_telegram_bot}", style="primary")])
    admin_kb_rows.append([
        InlineKeyboardButton(
            text=chr(0x1F4E4) + " إرسال الاشتراك للعميل",
            callback_data=f"sb_send_content:{order_id}",
            style="success",
        ),
    ])
    admin_kb_rows.append([
        InlineKeyboardButton(
            text=chr(0x274C) + " رفض الطلب",
            callback_data=f"sb_reject:{order_id}",
            style="danger",
        ),
    ])
    admin_markup = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    bot: Bot = message.bot
    for admin_id in _get_admin_ids():
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=admin_markup,
            )
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)
    await message.answer(
        NL.join([
            " <b>تم التحقق من العملية وإرسال طلبك بنجاح!</b>",
            "",
            f" المنتج: <b>{product_name}</b>",
            f" الكمية: <b>{qty}</b>",
            f" الاجمالي: <b>{_fmt(total)}</b>",
            f" رقم الطلب: <code>#{order_id}</code>",
            "",
            " <i>تم تأكيد الدفع تلقائيا. سيتم ارسال تفاصيل المنتج اليك قريبا.</i>",
        ]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" رجوع للمتجر", callback_data="shop", style="primary")],
        ]),
    )
    await state.clear()


'@
    if ((Count-Anchor $sbText $wAnchor) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier wrong-type handler (found $((Count-Anchor $sbText $wAnchor)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($wAnchor, $wNew + $wAnchor)
    Write-Host "supplier: TxID handler inserted."
}

Write-Text $sbPath $sbText $sb.Crlf
Write-Host "supplier: written."

# ---------------- 4) syntax check ----------------
python -m py_compile bot\misc\services\txid_verify.py bot\handlers\user\balance_and_payment.py bot\handlers\user\local_buy.py bot\handlers\user\supplier_buy.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "py_compile FAILED - review the output above."
    return
}
Write-Host "OK: patch applied and all files compile."
