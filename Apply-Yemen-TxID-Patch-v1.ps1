# Apply-Yemen-TxID-Patch-v1.ps1
# Applies the Binance Pay / USDT TRC20 TxID auto-verification patch to the Yemen bot.
#
# Files touched:
#   bot/database/models/main.py               - append VerifiedTransaction model
#   bot/handlers/user/balance_and_payment.py  - imports + TxID text handler + instruction text
# The new module bot/misc/services/txid_verify.py comes from the zip itself.
#
# Safe: every edit is anchor-checked (exact count 1) before any write, and the
# script is idempotent - re-running it skips edits that are already applied.
# Run from the project root. It never calls exit; on a failed anchor it
# reports the problem and returns without writing that file.

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

# ---------------- 0) backup before any write ----------------
# The two files the patch touches are copied first, so a restore is one command.
$backupDir = "backup-2026-11-28-txid"
$backupFiles = @("bot\handlers\user\balance_and_payment.py", "bot\database\models\main.py")
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $backupFiles) {
    if (-not (Test-Path $f)) { Write-Host "MISSING FILE: $f"; return }
    Copy-Item $f "$backupDir\" -Force
}
Write-Host "Backup saved in $backupDir."
Write-Host "To restore: Copy-Item $backupDir\balance_and_payment.py bot\handlers\user\ -Force; Copy-Item $backupDir\main.py bot\database\models\ -Force; git add bot; git commit -m 'restore before TxID patch'; git push"

# ---------------- 1) models: VerifiedTransaction ----------------
$modelsPath = "bot\database\models\main.py"
if (-not (Test-Path $modelsPath)) { Write-Host "MISSING FILE: $modelsPath"; return }
$models = Read-Text $modelsPath
$modelsText = $models.Text

if ($modelsText.Contains("class VerifiedTransaction")) {
    Write-Host "models: VerifiedTransaction already present - skipped."
} else {
    $modelsAnchor = @'
    def __str__(self):
        return f"#{self.id} - {self.amount_requested}$ ({self.status})"
'@
    if ((Count-Anchor $modelsText $modelsAnchor) -ne 1) {
        Write-Host "ANCHOR FAILED: models TopUpRequest tail (found $((Count-Anchor $modelsText $modelsAnchor)), need 1). Nothing written to $modelsPath."
        return
    }
    $verifiedClass = @'

class VerifiedTransaction(Database.BASE):
    """A Binance Pay / USDT TRC20 transaction ID already used for an auto top-up.

    The unique txid prevents the same transaction from crediting the
    balance twice. The row also lets an admin recover a credit that was
    verified but failed to apply.
    """
    __tablename__ = "verified_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # binance_pay | tron_usdt
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"),
        nullable=False, index=True
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    used_for: Mapped[str] = mapped_column(String(30), nullable=False, default="topup")
    verified_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __str__(self):
        return f"{self.txid} - {self.amount_usd}$ ({self.method})"
'@
    $modelsText = $modelsText.TrimEnd() + "`n" + $verifiedClass
    Write-Text $modelsPath $modelsText $models.Crlf
    Write-Host "models: VerifiedTransaction appended."
}

# ---------------- 2) balance_and_payment.py ----------------
$payPath = "bot\handlers\user\balance_and_payment.py"
if (-not (Test-Path $payPath)) { Write-Host "MISSING FILE: $payPath"; return }
$pay = Read-Text $payPath
$payText = $pay.Text

# 2a) admin_balance_change import
if ($payText.Contains("admin_balance_change")) {
    Write-Host "payment: admin_balance_change import already present - skipped."
} else {
    $aOld = "from bot.database.methods.transactions import prepare_stackvault_purchase, finalize_stackvault_purchase"
    $aNew = "from bot.database.methods.transactions import prepare_stackvault_purchase, finalize_stackvault_purchase, admin_balance_change"
    if ((Count-Anchor $payText $aOld) -ne 1) {
        Write-Host "ANCHOR FAILED: transactions import (found $((Count-Anchor $payText $aOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($aOld, $aNew)
    Write-Host "payment: admin_balance_change import added."
}

# 2b) txid_verify import
if ($payText.Contains("from bot.misc.services.txid_verify import")) {
    Write-Host "payment: txid_verify import already present - skipped."
} else {
    $bOld = "from bot.misc.services import CryptoPayAPI, CryptoPayAPIError, send_stars_invoice, send_fiat_invoice, StackVaultClient, StackVaultAPIError"
    $bNew = $bOld + "`nfrom bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid"
    if ((Count-Anchor $payText $bOld) -ne 1) {
        Write-Host "ANCHOR FAILED: services import (found $((Count-Anchor $payText $bOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($bOld, $bNew)
    Write-Host "payment: txid_verify import added."
}

# 2c) VerifiedTransaction + IntegrityError imports (manual top-up section)
if ($payText.Contains("VerifiedTransaction")) {
    Write-Host "payment: VerifiedTransaction import already present - skipped."
} else {
    $cOld = @'
from bot.database.models.main import TopUpRequest, PaymentMethod, User
from bot.database.main import Database
from sqlalchemy import select
'@
    $cNew = @'
from bot.database.models.main import TopUpRequest, PaymentMethod, User, VerifiedTransaction
from bot.database.main import Database
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
'@
    if ((Count-Anchor $payText $cOld) -ne 1) {
        Write-Host "ANCHOR FAILED: manual-section imports (found $((Count-Anchor $payText $cOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($cOld, $cNew)
    Write-Host "payment: VerifiedTransaction + IntegrityError imports added."
}

# 2d) instruction text in pay_manual_show_account
if ($payText.Contains("_txid_kind = classify_txid_method")) {
    Write-Host "payment: TxID instruction already present - skipped."
} else {
    $dOld = @'
    await state.update_data(payment_method_id=method_id, payment_method_name=pm.name)

    _entered_txt, _usd_txt = _topup_display(data)
    await call.message.edit_text(
        f" <b>{pm.name}</b>\n\n"
        f" المبلغ: <b>{_entered_txt}</b>\n\n"
        f" <b>رقم الحساب / التعليمات:</b>\n"
        f"<code>{pm.account_info}</code>\n\n"
        f" قم بتحويل المبلغ، ثم أرسل <b>صورة سند الحوالة</b> هنا.",
        parse_mode="HTML"
    )
'@
    $dNew = @'
    await state.update_data(payment_method_id=method_id, payment_method_name=pm.name)

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
    if ((Count-Anchor $payText $dOld) -ne 1) {
        Write-Host "ANCHOR FAILED: show_account block (found $((Count-Anchor $payText $dOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($dOld, $dNew)
    Write-Host "payment: TxID instruction added to show_account."
}

# 2e) TxID text handler, inserted before the photo handler
if ($payText.Contains("async def pay_manual_txid")) {
    Write-Host "payment: pay_manual_txid already present - skipped."
} else {
    $eAnchor = "@router.message(BalanceStates.waiting_receipt, F.photo)"
    if ((Count-Anchor $payText $eAnchor) -ne 1) {
        Write-Host "ANCHOR FAILED: photo handler line (found $((Count-Anchor $payText $eAnchor)), need 1). Nothing written to $payPath."
        return
    }
    $txidHandler = @'
@router.message(BalanceStates.waiting_receipt, F.text)
async def pay_manual_txid(message: Message, state: FSMContext):
    """Verify a pasted TxID (Binance Pay / USDT TRC20) and auto-credit the balance.

    Photo receipts stay the path for Yemeni wallets (no API); Binance and
    crypto are verified automatically and never need a photo.
    """
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    data = await state.get_data()
    amount_raw = data.get("amount")
    pm_id = data.get("payment_method_id")
    pm_name = data.get("payment_method_name") or ""
    if not amount_raw or not pm_id:
        await message.answer(" انتهت الجلسة. ابدأ الشحن من جديد.")
        await state.clear()
        return
    kind = classify_txid_method(pm_name)
    if not kind:
        await message.answer(" هذه الطريقة تحتاج صورة سند الحوالة - أرسل الصورة هنا.")
        return
    txid = text
    if not validate_txid_format(kind, txid):
        if kind == "binance_pay":
            hint = "ايدي عملية بينانس يبدأ بـ M_P_ - انسخه من تفاصيل التحويل في تطبيق بينانس."
        else:
            hint = "رقم عملية ترون 64 حرفا (أرقام وحروف a-f) - انسخه من محفظتك."
        await message.answer(f" رقم العملية غير صحيح. {hint}")
        return
    try:
        expected = Decimal(str(amount_raw))
    except Exception:
        await message.answer(" انتهت الجلسة. ابدأ الشحن من جديد.")
        await state.clear()
        return
    # Ensure the replay-protection table exists (no-op if Alembic made it).
    async with Database().session() as session:
        await session.run_sync(lambda s: VerifiedTransaction.__table__.create(s, checkfirst=True))
        await session.commit()
    # Replay protection: reject an already-used TxID before calling the APIs.
    async with Database().session() as session:
        used = (await session.execute(
            select(VerifiedTransaction).where(VerifiedTransaction.txid == txid)
        )).scalar_one_or_none()
        if used is not None:
            await message.answer(" هذا الايدي مستخدم من قبل - لا يمكن شحن الرصيد به مرة أخرى.")
            return
    try:
        if kind == "binance_pay":
            ok, received, reason = await verify_binance_pay_txid(txid, expected)
        else:
            ok, received, reason = await verify_tron_usdt_txid(txid, expected)
    except Exception as e:
        logger.warning("TxID verify failed for %s: %s", txid, e)
        await message.answer(" تعذر التحقق الآن - حاول بعد دقيقة، أو أرسل صورة السند.")
        return
    if not ok:
        if reason == "not_found":
            await message.answer(" لم أجد هذه العملية بعد - تأكد من نسخ الايدي كاملا، وإن كنت حوّلت للتو انتظر دقيقة ثم أعد إرساله.")
        elif reason == "wrong_receiver":
            await message.answer(" هذه العملية لم تصل إلى حساب المتجر - تأكد أنك حوّلت إلى الرقم/العنوان الصحيح.")
        elif reason == "amount_low":
            arrived = f"${received}" if received is not None else "مبلغ أقل"
            await message.answer(f" المبلغ المحوَّل ({arrived}) أقل من المطلوب (${expected}) - أكمل الفرق بعملية جديدة أو أرسل صورة السند.")
        elif reason == "wrong_type":
            await message.answer(" هذه العملية ليست تحويل Binance Pay بين مستخدمين - تأكد من الايدي.")
        elif reason == "tx_failed":
            await message.answer(" هذه العملية موجودة لكنها فاشلة على الشبكة - تحقق من محفظتك.")
        elif reason == "not_configured":
            await message.answer(" التحقق التلقائي غير مفعّل بعد - أرسل صورة السند.")
        else:
            await message.answer(" تعذر التحقق الآن - حاول بعد دقيقة، أو أرسل صورة السند.")
        return
    # Record the TxID first (unique): a second use is rejected even if credit fails below.
    try:
        async with Database().session() as session:
            session.add(VerifiedTransaction(
                txid=txid, method=kind, user_id=message.from_user.id,
                amount_usd=received, used_for="topup",
            ))
            await session.commit()
    except IntegrityError:
        await message.answer(" هذا الايدي مستخدم من قبل - لا يمكن شحن الرصيد به مرة أخرى.")
        return
    except Exception as e:
        logger.warning("VerifiedTransaction insert failed for %s: %s", txid, e)
        await message.answer(" تم التحقق لكن تعذر تسجيل العملية - تواصل مع الدعم.")
        return
    credited, _msg = await admin_balance_change(message.from_user.id, received)
    if not credited:
        await message.answer(" تم التحقق من العملية وسُجلت، لكن تعذر شحن الرصيد - تواصل مع الدعم وسيتم الشحن يدويا.")
        await state.clear()
        return
    # New balance for the confirmation.
    new_balance = None
    try:
        async with Database().session() as session:
            _u = (await session.execute(select(User).where(User.telegram_id == message.from_user.id))).scalar_one_or_none()
            if _u is not None and _u.balance is not None:
                new_balance = _u.balance
    except Exception:
        new_balance = None
    _entered_txt, _usd_txt = _topup_display(data)
    _bal_line = f"\n رصيدك الآن: <b>${new_balance}</b>" if new_balance is not None else ""
    await message.answer(
        f" تم التحقق من العملية!\n\n"
        f" شُحن رصيدك بـ <b>${received}</b> (المطلوب كان {_entered_txt}).{_bal_line}",
        parse_mode="HTML",
    )
    for admin_id in _get_admin_ids_topup():
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=(
                    f" شحن تلقائي موثّق\n\n"
                    f" المستخدم: <code>{message.from_user.id}</code>\n"
                    f" المبلغ: <b>${received}</b>\n"
                    f" الطريقة: <b>{pm_name}</b>\n"
                    f" العملية: <code>{txid}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Admin auto-topup notify failed: %s", e)
    await state.clear()


'@
    $payText = $payText.Replace($eAnchor, $txidHandler + $eAnchor)
    Write-Host "payment: pay_manual_txid handler inserted."
}

Write-Text $payPath $payText $pay.Crlf
Write-Host "payment: written."

# ---------------- 3) syntax check ----------------
if (-not (Test-Path "bot\misc\services\txid_verify.py")) {
    Write-Host "MISSING: bot\misc\services\txid_verify.py - extract the zip into the project root first."
    return
}
python -m py_compile bot\misc\services\txid_verify.py bot\handlers\user\balance_and_payment.py bot\database\models\main.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "py_compile FAILED - review the output above."
    return
}
Write-Host "OK: patch applied and all files compile."
