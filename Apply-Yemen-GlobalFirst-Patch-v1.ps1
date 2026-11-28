# Apply-Yemen-GlobalFirst-Patch-v1.ps1  v1.0
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-GlobalFirst-Patch-v1.ps1
#
# Global first impression (Yemen bot now serves Yemen + the world):
#   1. Bilingual /start welcome (Arabic + English together) in
#      _build_welcome_text.
#   2. Bilingual welcome after the channel check in
#      _build_check_welcome_text (adds Binance/crypto to the payment
#      list, "in Yemen and worldwide").
#   3. A Language button (the one bilingual label) in the channel gate
#      (check_sub), and the gate lets language buttons through.
#   4. After a language is picked, a user who has not joined the
#      channel sees the gate again (in the new language), never the
#      menu - so the gate cannot be bypassed by switching language.
#   5. Russian removed (ar + en only).
#
# Writes NOTHING if any anchor check fails. All edits are in-memory
# until every check passes, then written once.
$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Yemen-GlobalFirst-Patch v1.0"

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

function Remove-LineContaining {
    param([string]$Path, [string]$Needle, [string]$Label)
    $text = Get-Text $Path
    $lines = $text -split "`n"
    $hits = @($lines | Where-Object { $_ -like "*$Needle*" })
    if ($hits.Count -ne 1) {
        Write-Host "SKIP [$Label]: found $($hits.Count) lines with '$Needle' (want 1)"
        $script:failed += $Label
        return
    }
    $kept = @($lines | Where-Object { $_ -notlike "*$Needle*" })
    $script:edits += @{ Path = $Path; RemoveLine = $Needle; Label = $Label }
    Write-Host "OK   [$Label]"
}

# ---- Locate the project root (the script runs from the repo root) ----
$root = (Get-Location).Path
$handler = Join-Path $root "bot\handlers\user\main.py"
$inline  = Join-Path $root "bot\keyboards\inline.py"
$i18n    = Join-Path $root "bot\i18n\main.py"
foreach ($f in @($handler, $inline, $i18n)) {
    if (-not (Test-Path $f)) { Write-Host "STOP: not found: $f"; return }
}
if (-not (Assert-Utf8 $handler "handler-utf8")) { return }
if (-not (Assert-Utf8 $inline "inline-utf8")) { return }
if (-not (Assert-Utf8 $i18n "i18n-utf8")) { return }

# ---- Backups (distinct names: three files, three different paths) ----
$stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
$backupDir = Join-Path $root "backup-$stamp-globalfirst"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $handler (Join-Path $backupDir "handler-main.py")
Copy-Item $inline  (Join-Path $backupDir "inline.py")
Copy-Item $i18n    (Join-Path $backupDir "i18n-main.py")
Write-Host "Backup: $backupDir"

# ============ 1. Bilingual /start welcome: name + purchases ============
Replace-Anchor -Path $handler -Label "welcome-names-purchases" -Anchor @'
    name = (first_name or "").strip() or "عزيزي"

    if purchases_count == 0:
        purchases_str = "لا توجد مشتريات"
    elif purchases_count == 1:
        purchases_str = "مشترى واحد"
    elif purchases_count == 2:
        purchases_str = "مشترى اثنان"
    elif purchases_count <= 10:
        purchases_str = f"{purchases_count} مشتريات"
    else:
        purchases_str = f"{purchases_count} مشترى"
'@ -New @'
    name = (first_name or "").strip() or "عزيزي"
    name_en = (first_name or "").strip()
    greeting_en = f"Welcome, {name_en}!" if name_en else "Welcome!"

    if purchases_count == 0:
        purchases_str = "لا توجد مشتريات"
        purchases_en = "No purchases"
    elif purchases_count == 1:
        purchases_str = "مشترى واحد"
        purchases_en = "1 purchase"
    elif purchases_count == 2:
        purchases_str = "مشترى اثنان"
        purchases_en = "2 purchases"
    elif purchases_count <= 10:
        purchases_str = f"{purchases_count} مشتريات"
        purchases_en = f"{purchases_count} purchases"
    else:
        purchases_str = f"{purchases_count} مشترى"
        purchases_en = f"{purchases_count} purchases"
'@

# ============ 2. Bilingual balance lines (SAR / YER) ============
Replace-Anchor -Path $handler -Label "welcome-balance" -Anchor @'
                    balance_extra = NL + f"\U0001F1F8\U0001F1E6 {_wsar_v} ريال سعودي" + NL + f"\U0001F1FE\U0001F1EA {_wyer_v} ريال يمني"
'@ -New @'
                    balance_extra = NL + f"\U0001F1F8\U0001F1E6 {_wsar_v} ريال سعودي | Saudi riyals" + NL + f"\U0001F1FE\U0001F1EA {_wyer_v} ريال يمني | Yemeni riyals"
'@

# ============ 3. Bilingual /start welcome lines ============
Replace-Anchor -Path $handler -Label "welcome-lines" -Anchor @'
    lines = [
        SEPARATOR,
        f"{EMOJI_STAR} <b>أهلاً بك، {name}!</b> {EMOJI_STAR}",
        SEPARATOR,
        f"{EMOJI_DIAMOND} رصيدك: <b>\U0001F1FA\U0001F1F8 {balance_str}$</b>{balance_extra}",
        f"{EMOJI_GIFT} مشترياتك: <b>{purchases_str}</b>",
        SEPARATOR,
        "",
        f"{EMOJI_ROCKET} <i>اختر من القائمة أدناه:</i>",
    ]
'@ -New @'
    lines = [
        SEPARATOR,
        f"{EMOJI_STAR} <b>أهلاً بك، {name}!</b> {EMOJI_STAR}",
        f"{EMOJI_STAR} <b>{greeting_en}</b> {EMOJI_STAR}",
        SEPARATOR,
        f"{EMOJI_DIAMOND} رصيدك | Balance: <b>\U0001F1FA\U0001F1F8 {balance_str}$</b>{balance_extra}",
        f"{EMOJI_GIFT} مشترياتك | Purchases: <b>{purchases_str} | {purchases_en}</b>",
        SEPARATOR,
        "",
        f"{EMOJI_ROCKET} <i>اختر من القائمة أدناه | Choose from the menu below:</i>",
    ]
'@

# ============ 4. Bilingual welcome after the channel check ============
Replace-Anchor -Path $handler -Label "check-welcome" -Anchor @'
    name = (first_name or "").strip() or "عزيزي"
    lines = [
        "\U0001F1FE\U0001F1EA <b>منصة اليمن الإلكترونية</b> \U0001F6D2",
        f"أهلاً بك، {name}! \U0001F44B",
        "",
        "وجهتك الأولى للاشتراكات الرقمية والذكاء الاصطناعي في اليمن \u26A1",
        "",
        "\u2728 <b>خدماتنا المميزة:</b>",
        "\U0001F916 اشتراكات الذكاء الاصطناعي (ChatGPT Plus \u2022 Gemini وغيرها)",
        "\U0001F3A8 أدوات التصميم والمونتاج (Canva Pro وغيرها)",
        "\U0001F3AC باقات وحسابات رقمية متنوعة بأسعار مخفضة",
        "",
        "\U0001F4B3 <b>طرق الدفع المحلية المباشرة:</b>",
        "\U0001F3E6 بنك الكريمي (ريال يمني YER \u2022 سعودي SAR \u2022 دولار USD)",
        "\U0001F4F1 المحافظ الإلكترونية: محفظة جيب | موبايل موني",
        "",
        "\U0001F680 تفعيل سريع | \U0001F91D مصداقية وأمان | \U0001F4AC دعم فني متواصل",
        "",
        "\U0001F447 اضغط زر <b>\u00ABالدخول إلى الاشتراكات\u00BB</b> بالأسفل، واختر اشتراكك وادفع بإحدى الطرق أعلاه.",
    ]
'@ -New @'
    name = (first_name or "").strip() or "عزيزي"
    name_en = (first_name or "").strip()
    greeting_en = f"Welcome, {name_en}!" if name_en else "Welcome!"
    lines = [
        "\U0001F1FE\U0001F1EA <b>منصة اليمن الإلكترونية | Yemen Platform</b> \U0001F6D2",
        f"أهلاً بك، {name}! \U0001F44B",
        f"{greeting_en} \U0001F44B",
        "",
        "وجهتك الأولى للاشتراكات الرقمية والذكاء الاصطناعي في اليمن والعالم \u26A1",
        "Your first destination for digital subscriptions and AI, in Yemen and worldwide \u26A1",
        "",
        "\u2728 <b>خدماتنا المميزة | Our featured services:</b>",
        "\U0001F916 اشتراكات الذكاء الاصطناعي (ChatGPT Plus \u2022 Gemini وغيرها)",
        "\U0001F916 AI subscriptions (ChatGPT Plus \u2022 Gemini and more)",
        "\U0001F3A8 أدوات التصميم والمونتاج (Canva Pro وغيرها)",
        "\U0001F3A8 Design and editing tools (Canva Pro and more)",
        "\U0001F3AC باقات وحسابات رقمية متنوعة بأسعار مخفضة",
        "\U0001F3AC Various digital plans and accounts at discounted prices",
        "",
        "\U0001F4B3 <b>طرق الدفع | Payment methods:</b>",
        "\U0001F3E6 بنك الكريمي (ريال يمني YER \u2022 سعودي SAR \u2022 دولار USD)",
        "\U0001F3E6 Kuraimi Bank (Yemeni riyal YER \u2022 Saudi riyal SAR \u2022 US dollar USD)",
        "\U0001F4F1 المحافظ الإلكترونية: محفظة جيب | موبايل موني",
        "\U0001F4F1 E-wallets: Jeeb wallet | Mobile Money",
        "\U0001F4B0 بينانس والعملات الرقمية | Binance and cryptocurrency",
        "",
        "\U0001F680 تفعيل سريع | \U0001F91D مصداقية وأمان | \U0001F4AC دعم فني متواصل",
        "\U0001F680 Fast activation | \U0001F91D Trust and security | \U0001F4AC Ongoing technical support",
        "",
        "\U0001F447 اضغط زر <b>\u00ABالدخول إلى الاشتراكات\u00BB</b> بالأسفل، واختر اشتراكك وادفع بإحدى الطرق أعلاه.",
        "\U0001F447 Tap the <b>\u00ABEnter subscriptions\u00BB</b> button below, choose your subscription and pay with one of the methods above.",
    ]
'@

# ============ 5. Gate middleware: language buttons are exempt ============
Replace-Anchor -Path $handler -Label "gate-docstring" -Anchor @'
    The check button itself ("sub_channel_done") is exempt: it is the
    only way back in after subscribing.
'@ -New @'
    The check button itself ("sub_channel_done") is exempt: it is the
    only way back in after subscribing. The language buttons
    ("choose_language" and "set_lang:*") are exempt too, so a new user
    can switch to English before joining; the language handler itself
    re-checks the subscription before showing the menu.
'@

Replace-Anchor -Path $handler -Label "gate-exempt" -Anchor @'
        if event.data == "sub_channel_done":
            return await handler(event, data)
'@ -New @'
        if event.data in ("sub_channel_done", "choose_language") or event.data.startswith("set_lang:"):
            return await handler(event, data)
'@

# ============ 6. Language picker docstring (ar, en) ============
Replace-Anchor -Path $handler -Label "lang-docstring" -Anchor @'
    Show language selection options (ar, en, ru).
'@ -New @'
    Show language selection options (ar, en).
'@

# ============ 7. After a language is picked, re-check the gate ============
Replace-Anchor -Path $handler -Label "set-lang-gate" -Anchor @'
    await call.answer(localize("language.changed"))

    role = await check_role_cached(user_id) or 0
    channel_username = _updates_channel_username()
    markup = await main_menu(role=role, channel=channel_username, helper=EnvKeys.HELPER_ID)
'@ -New @'
    await call.answer(localize("language.changed"))

    channel_username = _updates_channel_username()
    if channel_username:
        # A new user who has not joined yet must see the gate again
        # (in the language just chosen), never the menu.
        subscribed = await _is_subscribed(call.bot, channel_username, user_id)
        if subscribed is not True:
            await call.message.edit_text(
                localize("subscribe.prompt"),
                reply_markup=check_sub(channel_username),
            )
            await state.clear()
            return

    role = await check_role_cached(user_id) or 0
    markup = await main_menu(role=role, channel=channel_username, helper=EnvKeys.HELPER_ID)
'@

# ============ 8. Language button in the gate (check_sub) ============
Replace-Anchor -Path $inline -Label "gate-language-button" -Anchor @'
    checks the channel subscription.
    Gate buttons are fixed Arabic with the green (success) style.
'@ -New @'
    checks the channel subscription.
    Gate buttons are fixed Arabic with the green (success) style.
    The language button is the one bilingual button (the user has not
    chosen a language yet) and is blue (primary) so it reads as a
    setting, not a gate action.
'@

Replace-Anchor -Path $inline -Label "gate-language-button-row" -Anchor @'
        InlineKeyboardButton(text="\U0001F504 التحقق من الاشتراك", callback_data="sub_channel_done", style="success"),
    )
    return kb.as_markup()
'@ -New @'
        InlineKeyboardButton(text="\U0001F504 التحقق من الاشتراك", callback_data="sub_channel_done", style="success"),
    )
    kb.row(
        InlineKeyboardButton(text="\U0001F310 اللغة | Language", callback_data="choose_language", style="primary"),
    )
    return kb.as_markup()
'@

# ============ 9. Remove the Russian button (ar + en only) ============
Remove-LineContaining -Path $inline -Needle "set_lang:ru" -Label "remove-russian-button"

# ============ 10. Drop Russian from the supported locales ============
Replace-Anchor -Path $i18n -Label "locales-ar-en" -Anchor @'
SUPPORTED_LOCALES = ("ar", "en", "ru")
'@ -New @'
SUPPORTED_LOCALES = ("ar", "en")
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
        if ($e.ContainsKey("RemoveLine")) {
            $lines = $text -split "`n"
            $text = (($lines | Where-Object { $_ -notlike "*$($e.RemoveLine)*" }) -join "`n")
        } else {
            $text = $text.Replace($e.Anchor, $e.New)
        }
    }
    Set-Text $g.Name $text
}
Write-Host "Done. Files changed: bot/handlers/user/main.py, bot/keyboards/inline.py, bot/i18n/main.py"
Write-Host "Next: py_compile the three files, then git add/commit/push."
