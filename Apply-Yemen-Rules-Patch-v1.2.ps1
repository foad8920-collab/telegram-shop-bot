# Apply-Yemen-Rules-Patch-v1.2.ps1  v1.2
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-Rules-Patch-v1.2.ps1
#
# Three fixes in one patch (2026-11-28):
#   1. RESTORE the rules handler. The global-first zip (v1) contained
#      the handler file, and extracting it overwrote the live handler
#      with the pre-rules version - so the separator and the English
#      rules were lost. This patch puts the rules handler back
#      (separator + bilingual + chunking).
#   2. ENGLISH RULES. The handler now also reads the user's saved
#      language from the DB (source of truth). If either the current
#      locale or the saved language says English, the rules are
#      English - so a stale locale can no longer hide the English
#      rules.
#   3. REORDER. The last rule (no cash refund) moves to the middle
#      (position 12 of 23). The seeding sentinel is now the rule's
#      text AND its new position, because the text alone already
#      exists in the DB and would wrongly skip the reorder.
#
# The zip for this patch contains ONLY this script (no bot files), so
# extracting it cannot overwrite anything. The script patches the
# live files in place and backs them up first.
#
# Writes NOTHING if any anchor check fails. All edits are in-memory
# until every check passes, then written once.
$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Yemen-Rules-Patch v1.2"

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
$seed    = Join-Path $root "bot\misc\services\rules_seed.py"
foreach ($f in @($handler, $seed)) {
    if (-not (Test-Path $f)) { Write-Host "STOP: not found: $f"; return }
}
if (-not (Assert-Utf8 $handler "handler-utf8")) { return }
if (-not (Assert-Utf8 $seed "seed-utf8")) { return }

# ---- Backups (distinct names) ----
$stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
$backupDir = Join-Path $root "backup-$stamp-rules-v12"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $handler (Join-Path $backupDir "handler-main.py")
Copy-Item $seed    (Join-Path $backupDir "rules_seed.py")
Write-Host "Backup: $backupDir"

# ============ 1. Handler: import get_current_locale ============
Replace-Anchor -Path $handler -Label "import-get-locale" -Anchor @'
from bot.i18n import localize, set_current_locale, set_user_cache_locale, SUPPORTED_LOCALES
'@ -New @'
from bot.i18n import localize, set_current_locale, set_user_cache_locale, SUPPORTED_LOCALES, get_current_locale
'@

# ============ 2. Handler: restore the rules body (separator + bilingual + DB locale) ============
Replace-Anchor -Path $handler -Label "rules-body-restore" -Anchor @'
    rules_text = ""
    try:
        async with _DB().session() as session:
            result = await session.execute(
                _select(_BotRule).where(_BotRule.is_active == True).order_by(_BotRule.sort_order, _BotRule.id)
            )
            rules = list(result.scalars().all())
            if rules:
                lines = []
                for i, r in enumerate(rules, start=1):
                    lines.append(f"{i}. {_esc((r.text or '').strip())}")
                rules_text = " القوانين" + NL + NL + (NL + NL).join(lines)
    except Exception:
        rules_text = ""
'@ -New @'
    rules_text = ""
    chunks = []
    try:
        async with _DB().session() as session:
            result = await session.execute(
                _select(_BotRule).where(_BotRule.is_active == True).order_by(_BotRule.sort_order, _BotRule.id)
            )
            rules = list(result.scalars().all())
            if rules:
                locale = get_current_locale()
                # The current locale is not always restored to the
                # user's saved language for a later update, so also
                # read the saved language from the DB (source of
                # truth). If either source says English, show English.
                try:
                    from bot.database.models.main import User as _User
                    _u = (await session.execute(
                        _select(_User).where(_User.telegram_id == call.from_user.id)
                    )).scalar_one_or_none()
                    if _u is not None and ((_u.language_code or "").strip().lower()[:2]) == "en":
                        locale = "en"
                except Exception:
                    pass
                title = "📜 Rules" if locale == "en" else "📜 القوانين"
                sep = "=" * 20
                blocks = []
                for i, r in enumerate(rules, start=1):
                    text = (r.text_en or "").strip() if locale == "en" else ""
                    if not text:
                        text = (r.text or "").strip()
                    blocks.append(f"{i}. {_esc(text)}")
                # Pack whole rules into messages that fit Telegram's
                # 4000-char safe limit (separator between rules).
                chunks = []
                current = title
                for block in blocks:
                    if current == title:
                        piece = NL + NL + block
                    else:
                        piece = NL + NL + sep + NL + NL + block
                    if len(current) + len(piece) > 4000 and current != title:
                        chunks.append(current)
                        current = title + NL + NL + block
                    else:
                        current += piece
                chunks.append(current)
    except Exception:
        rules_text = ""
        chunks = []
'@

# ============ 3. Handler: restore the chunked send block ============
Replace-Anchor -Path $handler -Label "rules-send-restore" -Anchor @'
    if not rules_text:
        rules_text = EnvKeys.RULES
    if rules_text:
        if len(rules_text) > 4000:
            rules_text = rules_text[:3990] + "..."
        await call.message.edit_text(rules_text, reply_markup=back("back_to_menu"))
    else:
        await call.answer(localize("rules.not_set"))
    await state.clear()
'@ -New @'
    if chunks:
        await call.message.edit_text(chunks[0], reply_markup=back("back_to_menu"))
        for chunk in chunks[1:]:
            await call.message.answer(chunk, reply_markup=back("back_to_menu"))
    else:
        if not rules_text:
            rules_text = EnvKeys.RULES
        if rules_text:
            if len(rules_text) > 4000:
                rules_text = rules_text[:3990] + "..."
            await call.message.edit_text(rules_text, reply_markup=back("back_to_menu"))
        else:
            await call.answer(localize("rules.not_set"))
    await state.clear()
'@

# ============ 4. Seed: take the no-refund rule off the end ============
Replace-Anchor -Path $seed -Label "reorder-remove-from-end" -Anchor @'
    (
        "لا يستطيع أي عميل استرداد المبالغ نقدا في حال حصل أي خلل؛ بل تُضاف المبالغ إلى رصيد محفظته في البوت ويستطيع شراء أي منتج آخر بها.",
        "No customer can receive a cash refund if any problem occurs. Instead, the amount is added to their wallet balance in the bot, and they can use it to buy any other product.",
    ),
]
'@ -New @'
]
'@

# ============ 5. Seed: put the no-refund rule at position 12 (after the warranty rule) ============
Replace-Anchor -Path $seed -Label "reorder-insert-at-12" -Anchor @'
    (
        "الضمان يشمل عيبا في التسليم نفسه خلال فترة الضمان المعلنة فقط، ولا يشمل انقطاع خدمة المزوّد أو تغيير مزاياه أو سوء استخدام العميل.",
        "The warranty covers only a defect in the delivery itself, during the stated warranty period. It does not cover interruptions of the provider's service, changes to its features, or your misuse.",
    ),
'@ -New @'
    (
        "الضمان يشمل عيبا في التسليم نفسه خلال فترة الضمان المعلنة فقط، ولا يشمل انقطاع خدمة المزوّد أو تغيير مزاياه أو سوء استخدام العميل.",
        "The warranty covers only a defect in the delivery itself, during the stated warranty period. It does not cover interruptions of the provider's service, changes to its features, or your misuse.",
    ),
    (
        "لا يستطيع أي عميل استرداد المبالغ نقدا في حال حصل أي خلل؛ بل تُضاف المبالغ إلى رصيد محفظته في البوت ويستطيع شراء أي منتج آخر بها.",
        "No customer can receive a cash refund if any problem occurs. Instead, the amount is added to their wallet balance in the bot, and they can use it to buy any other product.",
    ),
'@

# ============ 6. Seed: sentinel is now (text AND position 12) ============
Replace-Anchor -Path $seed -Label "sentinel-const" -Anchor @'
# Sentinel: rule 23's Arabic text. It MUST be a rule that does not exist
# in the old 18 rules - rule 1 cannot be the sentinel, because the old
# rule 1 is word-for-word identical to the new rule 1 (both name the
# Yemen platform), so the v1.0 seeding wrongly thought the 23 were
# already there and skipped, leaving the old 18 rules with no English.
RULE_23_AR = YEMEN_RULES[22][0]
'@ -New @'
# Sentinel: the no-cash-refund rule's Arabic text AND its position
# (12, the middle). The text alone is not enough any more: the rule
# already exists in the DB (it was rule 23), so a text-only check
# would wrongly skip the reorder. The (text, sort_order) pair is
# true only after the reorder has been applied.
NO_REFUND_AR = "لا يستطيع أي عميل استرداد المبالغ نقدا في حال حصل أي خلل؛ بل تُضاف المبالغ إلى رصيد محفظته في البوت ويستطيع شراء أي منتج آخر بها."
NO_REFUND_SORT = 12
'@

Replace-Anchor -Path $seed -Label "sentinel-query" -Anchor @'
        found = await session.execute(
            _sql_text("SELECT 1 FROM bot_rules WHERE text = :t LIMIT 1"),
            {"t": RULE_23_AR},
        )
'@ -New @'
        found = await session.execute(
            _sql_text(
                "SELECT 1 FROM bot_rules WHERE text = :t AND sort_order = :s LIMIT 1"
            ),
            {"t": NO_REFUND_AR, "s": NO_REFUND_SORT},
        )
'@

# ============ 7. Seed: docstring (the old one still says "rule 1") ============
Replace-Anchor -Path $seed -Label "seed-docstring" -Anchor @'
REPLACE mode, guarded by a sentinel: if rule 1's Arabic text is already
in the table, nothing is touched, so rules the owner adds later from the
panel are safe. The function also adds the text_en column if it is
missing (the model patch adds it too; the raw SQL here works even
before the model is reloaded).
'@ -New @'
REPLACE mode, guarded by a sentinel: the seeding runs only if the
no-cash-refund rule is NOT already at position 12 (the middle). Once
it is there, nothing is touched, so rules the owner adds later from
the panel are safe. The function also adds the text_en column if it
is missing (the model patch adds it too; the raw SQL here works even
before the model is reloaded).
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
Write-Host "Done. Files changed: bot/handlers/user/main.py, bot/misc/services/rules_seed.py"
Write-Host "Next: py_compile both files, then git add/commit/push."
Write-Host "After deploy, the seeding re-orders the rules (watch the Render log for 'Seeded 23 bilingual rules')."
