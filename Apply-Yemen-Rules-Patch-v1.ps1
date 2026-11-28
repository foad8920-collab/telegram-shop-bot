# Apply-Yemen-Rules-Patch-v1.ps1  v1.1
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-Rules-Patch-v1.ps1
# v1.1: the seeding sentinel is rule 23 (v1.0 used rule 1, which is
# word-for-word identical to the old rule 1, so the seeding skipped and
# the old 18 rules stayed with no English).
#
# The 23 bilingual rules for the Yemen bot (merge from SubWorld):
#   - BotRule gets a text_en column (English text, shown when the user's
#     language is English; Arabic is the fallback when it is empty).
#   - The panel's rules page gains the English text field.
#   - The rules handler picks the language, puts a "===================="
#     line between rules (the same line as in the payment messages),
#     keeps the blank-line spacing exactly as it is, and splits the
#     rules into whole-rule messages when they pass 4000 chars (the
#     English rules do - 2 messages; the Arabic rules fit in one).
#   - The 23 rules are seeded once at startup (REPLACE mode, guarded by
#     a sentinel, so rules the owner adds later from the panel are safe).
#     The seeding module comes from the zip itself.
#
# Files touched:
#   bot/database/models/main.py - BotRule.text_en
#   bot/web/admin.py            - BotRuleAdmin: text_en in list/labels/form/search
#   bot/handlers/user/main.py   - rules handler: locale + separator + split
#   bot/main.py                 - guarded seeding call after register_models()
# The new bot/misc/services/rules_seed.py comes from the zip itself.
#
# Safe: every edit is anchor-checked (exact count) before any write, the
# script is idempotent - re-running it skips edits already applied - and
# it never calls exit; on a failed anchor it reports the problem and
# returns without writing that file. A backup of all four files is saved
# first (restore commands are printed at the end of the run).

$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Yemen-Rules-Patch v1.1"

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
    # True only if the whole file decodes as valid UTF-8.
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $strict = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $null = $strict.GetString($bytes)
        return $true
    } catch {
        return $false
    }
}

# ---------------- 0) backup before any write ----------------
# NOTE: three of the four files are all named main.py, so they are
# backed up under distinct names - a plain copy would overwrite them.
$backupDir = "backup-2026-11-28-rules"
if (Test-Path $backupDir) { $backupDir = "backup-2026-11-28-rules-v2" }
$backupMap = @(
    @{ Src = "bot\database\models\main.py"; Dst = "models-main.py" },
    @{ Src = "bot\web\admin.py"; Dst = "admin.py" },
    @{ Src = "bot\handlers\user\main.py"; Dst = "handler-main.py" },
    @{ Src = "bot\main.py"; Dst = "entrypoint-main.py" }
)
$backupFiles = @("bot\database\models\main.py", "bot\web\admin.py", "bot\handlers\user\main.py", "bot\main.py")
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($m in $backupMap) {
    if (-not (Test-Path $m.Src)) { Write-Host "MISSING FILE: $($m.Src) - run from the repo root."; return }
    Copy-Item $m.Src "$backupDir\$($m.Dst)" -Force
}
Write-Host "Backup saved in $backupDir."
Write-Host "To restore: Copy-Item $backupDir\models-main.py bot\database\models\main.py -Force; Copy-Item $backupDir\admin.py bot\web\admin.py -Force; Copy-Item $backupDir\handler-main.py bot\handlers\user\main.py -Force; Copy-Item $backupDir\entrypoint-main.py bot\main.py -Force; git add bot; git commit -m 'restore before rules patch'; git push"

# Guards: the new rules_seed.py (with the 23 rules) must be in place, and
# all four patched files must be valid UTF-8.
if (-not (Test-Path "bot\misc\services\rules_seed.py")) {
    Write-Host "MISSING: bot\misc\services\rules_seed.py - extract the zip into the project root first."
    return
}
$seed = [System.IO.File]::ReadAllText("bot\misc\services\rules_seed.py", [System.Text.Encoding]::UTF8)
if (-not $seed.Contains("YEMEN_RULES")) {
    Write-Host "STOP: rules_seed.py has no YEMEN_RULES - the v1 zip was not extracted (old file still there). Extract the zip with -Force, then re-run."
    return
}
foreach ($f in $backupFiles) {
    if (-not (Test-Utf8 $f)) {
        Write-Host "STOP: $f is not valid UTF-8. Re-save it as UTF-8 in your editor, then re-run. Nothing was changed."
        return
    }
}

# ---------------- 1) bot/database/models/main.py: text_en ----------------
$mdPath = "bot\database\models\main.py"
$md = Read-Text $mdPath
$mdText = $md.Text

if ($mdText.Contains("text_en: Mapped[str]")) {
    Write-Host "models: text_en already present - skipped."
} else {
    $mOld = @'
class BotRule(Database.BASE):
    """Bot rules shown to users, managed from the admin panel."""
    __tablename__ = 'bot_rules'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
'@
    $mNew = @'
class BotRule(Database.BASE):
    """Bot rules shown to users, managed from the admin panel."""
    __tablename__ = 'bot_rules'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_en: Mapped[str] = mapped_column(Text, nullable=True)
'@
    if ((Count-Anchor $mdText $mOld) -ne 1) {
        Write-Host "ANCHOR FAILED: models BotRule (found $((Count-Anchor $mdText $mOld)), need 1). Nothing written to $mdPath."
        return
    }
    $mdText = $mdText.Replace($mOld, $mNew)
    Write-Host "models: text_en added."
}

Write-Text $mdPath $mdText $md.Crlf
Write-Host "models: written."

# ---------------- 2) bot/web/admin.py: BotRuleAdmin ----------------
$admPath = "bot\web\admin.py"
$adm = Read-Text $admPath
$admText = $adm.Text

if ($admText.Contains("BotRule.text_en")) {
    Write-Host "admin: text_en already present - skipped."
} else {
    $aOld = @'
        BotRule.text,
        BotRule.sort_order,
'@
    $aNew = @'
        BotRule.text,
        BotRule.text_en,
        BotRule.sort_order,
'@
    if ((Count-Anchor $admText $aOld) -ne 1) {
        Write-Host "ANCHOR FAILED: admin column_list (found $((Count-Anchor $admText $aOld)), need 1). Nothing written to $admPath."
        return
    }
    $admText = $admText.Replace($aOld, $aNew)
    Write-Host "admin: column_list updated."

    $lOld = '        "text": "نص القانون",'
    $lNew = '        "text": "نص القانون",
        "text_en": "النص الإنجليزي",'
    if ((Count-Anchor $admText $lOld) -ne 1) {
        Write-Host "ANCHOR FAILED: admin column_labels (found $((Count-Anchor $admText $lOld)), need 1). Nothing written to $admPath."
        return
    }
    $admText = $admText.Replace($lOld, $lNew)
    Write-Host "admin: column_labels updated."

    $fOld = '    form_columns = ["text", "sort_order", "is_active"]'
    $fNew = '    form_columns = ["text", "text_en", "sort_order", "is_active"]'
    if ((Count-Anchor $admText $fOld) -ne 1) {
        Write-Host "ANCHOR FAILED: admin form_columns (found $((Count-Anchor $admText $fOld)), need 1). Nothing written to $admPath."
        return
    }
    $admText = $admText.Replace($fOld, $fNew)
    Write-Host "admin: form_columns updated."

    $sOld = '    column_searchable_list = [BotRule.text]'
    $sNew = '    column_searchable_list = [BotRule.text, BotRule.text_en]'
    if ((Count-Anchor $admText $sOld) -ne 1) {
        Write-Host "ANCHOR FAILED: admin searchable (found $((Count-Anchor $admText $sOld)), need 1). Nothing written to $admPath."
        return
    }
    $admText = $admText.Replace($sOld, $sNew)
    Write-Host "admin: searchable updated."
}

Write-Text $admPath $admText $adm.Crlf
Write-Host "admin: written."

# ---------------- 3) bot/handlers/user/main.py: rules handler ----------------
$hdPath = "bot\handlers\user\main.py"
$hd = Read-Text $hdPath
$hdText = $hd.Text

if ($hdText.Contains("get_current_locale")) {
    Write-Host "handler: get_current_locale import already present - skipped."
} else {
    $pOld = "from bot.i18n import localize, set_current_locale, set_user_cache_locale, SUPPORTED_LOCALES"
    $pNew = "from bot.i18n import localize, set_current_locale, set_user_cache_locale, SUPPORTED_LOCALES, get_current_locale"
    if ((Count-Anchor $hdText $pOld) -ne 1) {
        Write-Host "ANCHOR FAILED: handler i18n import (found $((Count-Anchor $hdText $pOld)), need 1). Nothing written to $hdPath."
        return
    }
    $hdText = $hdText.Replace($pOld, $pNew)
    Write-Host "handler: get_current_locale import added."
}

if ($hdText.Contains('sep = "=" * 20')) {
    Write-Host "handler: rules body already patched - skipped."
} else {
    $hOld = @'
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
'@
    $hNew = @'
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
    if ((Count-Anchor $hdText $hOld) -ne 1) {
        Write-Host "ANCHOR FAILED: handler rules body (found $((Count-Anchor $hdText $hOld)), need 1). Nothing written to $hdPath."
        return
    }
    $hdText = $hdText.Replace($hOld, $hNew)
    Write-Host "handler: rules body updated."
}

if ($hdText.Contains("for chunk in chunks[1:]:")) {
    Write-Host "handler: send block already patched - skipped."
} else {
    $sOld = @'
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
    $sNew = @'
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
    if ((Count-Anchor $hdText $sOld) -ne 1) {
        Write-Host "ANCHOR FAILED: handler send block (found $((Count-Anchor $hdText $sOld)), need 1). Nothing written to $hdPath."
        return
    }
    $hdText = $hdText.Replace($sOld, $sNew)
    Write-Host "handler: send block updated."
}

Write-Text $hdPath $hdText $hd.Crlf
Write-Host "handler: written."

# ---------------- 4) bot/main.py: seeding call after register_models() ----------------
$epPath = "bot\main.py"
$ep = Read-Text $epPath
$epText = $ep.Text

if ($epText.Contains("ensure_yemen_rules_seeded")) {
    Write-Host "entrypoint: seeding call already present - skipped."
} else {
    $eOld = @'
    await register_models()
'@
    $eNew = @'
    await register_models()

    # Seed the 23 bilingual rules once (REPLACE mode, sentinel-guarded).
    # Guarded so a seeding failure can never break startup.
    try:
        from bot.misc.services.rules_seed import ensure_yemen_rules_seeded

        await ensure_yemen_rules_seeded()
    except Exception:
        logging.exception("Rules seeding failed - existing rules unaffected")
'@
    if ((Count-Anchor $epText $eOld) -ne 1) {
        Write-Host "ANCHOR FAILED: entrypoint register_models (found $((Count-Anchor $epText $eOld)), need 1). Nothing written to $epPath."
        return
    }
    $epText = $epText.Replace($eOld, $eNew)
    Write-Host "entrypoint: seeding call added."
}

Write-Text $epPath $epText $ep.Crlf
Write-Host "entrypoint: written."

# ---------------- 5) syntax check ----------------
python -m py_compile bot\database\models\main.py bot\web\admin.py bot\handlers\user\main.py bot\main.py bot\misc\services\rules_seed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "py_compile FAILED - review the output above."
    return
}
Write-Host "OK: rules patch applied and all files compile."
