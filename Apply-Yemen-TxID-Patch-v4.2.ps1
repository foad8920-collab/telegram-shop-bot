# Apply-Yemen-TxID-Patch-v4.2.ps1
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-TxID-Patch-v4.2.ps1
#
# v4.2: the same copy fix for the Yemeni wallets. v4/v4.1 fixed Binance and
# Crypto (one value alone in <code>), but a wallet row holds several
# account numbers (YER / USD / SAR) plus rates and a name, so the whole
# block was still one code span and a tap copied everything. Now every
# long number (account number, 9+ digits) gets its own <code> span and
# the labels, rates and name stay plain text, in all three displays
# (top-up, local buy, supplier buy). Binance/Crypto keep the v4.1 layout.
#
# Files touched:
#   bot/handlers/user/balance_and_payment.py - import + one-line display call
#   bot/handlers/user/local_buy.py           - import + one-line display call
#                                              + manual (wallet) branch
#   bot/handlers/user/supplier_buy.py        - import + one-line display call
# The extended bot/misc/services/txid_verify.py (new helper) comes from
# the zip itself.
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
if (Test-Path $backupDir) { $backupDir = "backup-2026-11-28-txid-v4.2" }
$backupFiles = @("bot\handlers\user\balance_and_payment.py", "bot\handlers\user\local_buy.py", "bot\handlers\user\supplier_buy.py")
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $backupFiles) {
    if (-not (Test-Path $f)) { Write-Host "MISSING FILE: $f"; return }
    Copy-Item $f "$backupDir\" -Force
}
Write-Host "Backup saved in $backupDir."
Write-Host "To restore: Copy-Item $backupDir\balance_and_payment.py bot\handlers\user\ -Force; Copy-Item $backupDir\local_buy.py bot\handlers\user\ -Force; Copy-Item $backupDir\supplier_buy.py bot\handlers\user\ -Force; git add bot; git commit -m 'restore before TxID patch v4.2'; git push"

# Guards: the new txid_verify.py (with the helper) must be in place, and
# all three patched files must be UTF-8.
if (-not (Test-Path "bot\misc\services\txid_verify.py")) {
    Write-Host "MISSING: bot\misc\services\txid_verify.py - extract the zip into the project root first."
    return
}
$txv = [System.IO.File]::ReadAllText("bot\misc\services\txid_verify.py", [System.Text.Encoding]::UTF8)
if (-not $txv.Contains("def account_info_display_lines")) {
    Write-Host "STOP: txid_verify.py has no account_info_display_lines - the v4.2 zip was not extracted (old file still there). Extract the zip with -Force, then re-run."
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

if ($payText.Contains("account_info_display_lines")) {
    Write-Host "payment: display helper import already present - skipped."
} else {
    $pOld = "from bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid, copyable_account_value, account_info_rest_lines, payment_method_order_key"
    $pNew = $pOld + ", account_info_display_lines"
    if ((Count-Anchor $payText $pOld) -ne 1) {
        Write-Host "ANCHOR FAILED: payment txid import (found $((Count-Anchor $payText $pOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($pOld, $pNew)
    Write-Host "payment: display helper import added."
}

if ($payText.Contains("account_info_display_lines(pm.name")) {
    Write-Host "payment: display call already present - skipped."
} else {
    $cOld = @'
    _copy_value = copyable_account_value(pm.name, pm.account_info)
    if _copy_value:
        _rest = account_info_rest_lines(pm.account_info, _copy_value)
        _account_block = f"<code>{_copy_value}</code>" + ("\n" + "\n".join(_rest) if _rest else "")
    else:
        _account_block = f"<code>{pm.account_info}</code>"
'@
    $cNew = @'
    _account_block = "\n".join(account_info_display_lines(pm.name, pm.account_info))
'@
    if ((Count-Anchor $payText $cOld) -ne 1) {
        Write-Host "ANCHOR FAILED: payment account block (found $((Count-Anchor $payText $cOld)), need 1). Nothing written to $payPath."
        return
    }
    $payText = $payText.Replace($cOld, $cNew)
    Write-Host "payment: display call added."
}

Write-Text $payPath $payText $pay.Crlf
Write-Host "payment: written."

# ---------------- 2) local_buy.py ----------------
$lbPath = "bot\handlers\user\local_buy.py"
$lb = Read-Text $lbPath
$lbText = $lb.Text

if ($lbText.Contains("account_info_display_lines")) {
    Write-Host "local: display helper import already present - skipped."
} else {
    $pOld = "from bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid, copyable_account_value, account_info_rest_lines, payment_method_order_key, TxVerifyError"
    $pNew = $pOld + ", account_info_display_lines"
    if ((Count-Anchor $lbText $pOld) -ne 1) {
        Write-Host "ANCHOR FAILED: local txid import (found $((Count-Anchor $lbText $pOld)), need 1). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($pOld, $pNew)
    Write-Host "local: display helper import added."
}

if ($lbText.Contains("account_info_display_lines(pm.name")) {
    Write-Host "local: display call already present - skipped."
} else {
    $bOld = @'
        _copy_value = copyable_account_value(pm.name, pm.account_info)
        _account_lines = []
        if _copy_value:
            _account_lines.append(f"<code>{_copy_value}</code>")
            _account_lines.extend(account_info_rest_lines(pm.account_info, _copy_value))
        else:
            _account_lines.append(f"<code>{pm.account_info}</code>")
'@
    $bNew = @'
        _account_lines = account_info_display_lines(pm.name, pm.account_info)
'@
    if ((Count-Anchor $lbText $bOld) -ne 1) {
        Write-Host "ANCHOR FAILED: local account lines (found $((Count-Anchor $lbText $bOld)), need 1). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($bOld, $bNew)
    Write-Host "local: display call added."
}

if ($lbText.Contains("*account_info_display_lines(pm.name, pm.account_info),")) {
    Write-Host "local: manual branch display already present - skipped."
} else {
    $mOld = '        f"<code>{pm.account_info}</code>",'
    $mNew = '        *account_info_display_lines(pm.name, pm.account_info),'
    if ((Count-Anchor $lbText $mOld) -ne 1) {
        Write-Host "ANCHOR FAILED: local manual branch (found $((Count-Anchor $lbText $mOld)), need 1). Nothing written to $lbPath."
        return
    }
    $lbText = $lbText.Replace($mOld, $mNew)
    Write-Host "local: manual branch display added."
}

Write-Text $lbPath $lbText $lb.Crlf
Write-Host "local: written."

# ---------------- 3) supplier_buy.py ----------------
$sbPath = "bot\handlers\user\supplier_buy.py"
$sb = Read-Text $sbPath
$sbText = $sb.Text

if ($sbText.Contains("account_info_display_lines")) {
    Write-Host "supplier: display helper import already present - skipped."
} else {
    $pOld = "from bot.misc.services.txid_verify import classify_txid_method, validate_txid_format, verify_binance_pay_txid, verify_tron_usdt_txid, copyable_account_value, account_info_rest_lines, payment_method_order_key, TxVerifyError"
    $pNew = $pOld + ", account_info_display_lines"
    if ((Count-Anchor $sbText $pOld) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier txid import (found $((Count-Anchor $sbText $pOld)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($pOld, $pNew)
    Write-Host "supplier: display helper import added."
}

if ($sbText.Contains("account_info_display_lines(pm.name")) {
    Write-Host "supplier: display call already present - skipped."
} else {
    $dOld = @'
    _copy_value = copyable_account_value(pm.name, pm.account_info)
    _account_lines = []
    if _copy_value:
        _account_lines.append(f"<code>{_copy_value}</code>")
        _account_lines.extend(account_info_rest_lines(pm.account_info, _copy_value))
    else:
        _account_lines.append(f"<code>{pm.account_info}</code>")
'@
    $dNew = @'
    _account_lines = account_info_display_lines(pm.name, pm.account_info)
'@
    if ((Count-Anchor $sbText $dOld) -ne 1) {
        Write-Host "ANCHOR FAILED: supplier account lines (found $((Count-Anchor $sbText $dOld)), need 1). Nothing written to $sbPath."
        return
    }
    $sbText = $sbText.Replace($dOld, $dNew)
    Write-Host "supplier: display call added."
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
