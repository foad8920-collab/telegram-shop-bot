# Apply-Yemen-GateLang-Patch-v1.ps1  v1.0
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-GateLang-Patch-v1.ps1
#
# Two fixes from the user's live test (2026-11-28 11:18):
#   1. Switching to English changed the gate prompt but NOT the gate
#      buttons (they were hardcoded Arabic in check_sub). The buttons
#      now follow the user's language.
#   2. The gate no longer forces Arabic. The three set_current_locale
#      ("ar") calls in the gate paths are removed, so the gate prompt
#      and buttons stay in the language the user chose. New users are
#      still Arabic (their language is set to "ar" at registration).
#
# The name confusion (description says "updates channel", the button
# says "news channel") is fixed in the BotFather description text
# given with this patch - the code already calls the button
# "قناة الأخبار" / "News channel" in both languages.
#
# Writes NOTHING if any anchor check fails. All edits are in-memory
# until every check passes, then written once.
$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Yemen-GateLang-Patch v1.0"

$script:failed = @()
$script:edits = @()

function Assert-Utf8 {
    param([string]$Path, [string]$Label)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    try {
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        $null = $strict.GetString($bytes)
    } catch {
        Write-Host "SKIP [$Label]: $Path is not valid UTF-8"
        $script:failed += $Label
        return $false
    }
    return $true
}

function Get-Text {
    param([string]$Path)
    return [System.IO.File]::ReadAllText($Path, $utf8NoBom)
}

function Set-Text {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

function Replace-Anchor {
    param([string]$Path, [string]$Anchor, [string]$New, [string]$Label)
    $text = Get-Text $Path
    $count = ([regex]::Matches($text, [regex]::Escape($Anchor))).Count
    if ($count -ne 1) {
        Write-Host "SKIP [$Label]: anchor found $count times (want 1)"
        $script:failed += $Label
        return
    }
    $script:edits += @{ Path = $Path; Anchor = $Anchor; New = $New; Label = $Label }
    Write-Host "OK   [$Label]"
}

# ---- Locate the project root (the script runs from the repo root) ----
$root = (Get-Location).Path
$handler = Join-Path $root "bot\handlers\user\main.py"
$inline  = Join-Path $root "bot\keyboards\inline.py"
foreach ($f in @($handler, $inline)) {
    if (-not (Test-Path $f)) { Write-Host "STOP: not found: $f"; return }
}
if (-not (Assert-Utf8 $handler "handler-utf8")) { return }
if (-not (Assert-Utf8 $inline "inline-utf8")) { return }

# ---- Backups (distinct names) ----
$stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
$backupDir = Join-Path $root "backup-$stamp-gatelang"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $handler (Join-Path $backupDir "handler-main.py")
Copy-Item $inline  (Join-Path $backupDir "inline.py")
Write-Host "Backup: $backupDir"

# ============ 1. inline.py: import get_current_locale ============
Replace-Anchor -Path $inline -Label "import-get-locale" -Anchor @'
from bot.i18n import localize
'@ -New @'
from bot.i18n import localize, get_current_locale
'@

# ============ 2. inline.py: gate buttons follow the language ============
Replace-Anchor -Path $inline -Label "gate-buttons-locale" -Anchor @'
    """
    checks the channel subscription.
    Gate buttons are fixed Arabic with the green (success) style.
    The language button is the one bilingual button (the user has not
    chosen a language yet) and is blue (primary) so it reads as a
    setting, not a gate action.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="\u2139\ufe0f قناة الأخبار", url=f"https://t.me/{channel_username}", style="success"),
        InlineKeyboardButton(text="\U0001F504 التحقق من الاشتراك", callback_data="sub_channel_done", style="success"),
    )
'@ -New @'
    """
    checks the channel subscription.
    The two gate buttons follow the user's language (Arabic by
    default, English after a language switch); the language button
    is the one bilingual button and is blue (primary) so it reads
    as a setting, not a gate action.
    """
    if get_current_locale() == "en":
        news_text = "\u2139\ufe0f News channel"
        verify_text = "\U0001F504 Verify subscription"
    else:
        news_text = "\u2139\ufe0f قناة الأخبار"
        verify_text = "\U0001F504 التحقق من الاشتراك"
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=news_text, url=f"https://t.me/{channel_username}", style="success"),
        InlineKeyboardButton(text=verify_text, callback_data="sub_channel_done", style="success"),
    )
'@

# ============ 3. Gate middleware: stop forcing Arabic ============
Replace-Anchor -Path $handler -Label "gate-middleware-locale" -Anchor @'
        if subscribed is True:
            return await handler(event, data)
        set_current_locale("ar")
        try:
            await event.answer(localize("subscribe.prompt"), show_alert=True)
'@ -New @'
        if subscribed is True:
            return await handler(event, data)
        # The gate follows the user's chosen language (Arabic by
        # default); it is no longer forced to Arabic.
        try:
            await event.answer(localize("subscribe.prompt"), show_alert=True)
'@

# ============ 4. /start gate path: stop forcing Arabic ============
Replace-Anchor -Path $handler -Label "start-gate-locale" -Anchor @'
        if subscribed is not True:
            set_current_locale("ar")
            await message.answer(localize("subscribe.prompt"), reply_markup=check_sub(channel_username))
'@ -New @'
        if subscribed is not True:
            await message.answer(localize("subscribe.prompt"), reply_markup=check_sub(channel_username))
'@

# ============ 5. Verify-button handler: stop forcing Arabic ============
Replace-Anchor -Path $handler -Label "verify-gate-locale" -Anchor @'
            await state.clear()
            return

    set_current_locale("ar")
    await call.answer(localize("subscribe.prompt"), show_alert=True)


# --- Operation History ---
'@ -New @'
            await state.clear()
            return

    await call.answer(localize("subscribe.prompt"), show_alert=True)


# --- Operation History ---
'@

if ($script:failed.Count -gt 0) {
    Write-Host "FAILED checks: $($script:failed -join ', ') - nothing was written."
    return
}

# ---- Apply all edits (in memory), then write each file once ----
$grouped = $script:edits | Group-Object Path
foreach ($g in $grouped) {
    $text = Get-Text $g.Name
    foreach ($e in $g.Group) {
        $text = $text.Replace($e.Anchor, $e.New)
    }
    Set-Text $g.Name $text
}
Write-Host "Done. Files changed: bot/handlers/user/main.py, bot/keyboards/inline.py"
Write-Host "Next: py_compile the two files, then git add/commit/push."
Write-Host "Then set the new BotFather description (text given with this patch)."
