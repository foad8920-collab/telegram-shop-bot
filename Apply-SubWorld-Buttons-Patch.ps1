# Apply-SubWorld-Buttons-Patch.ps1 - VERIFY ONLY (no file edits).
# 1) Extract subworld-buttons-v1.zip into the project root first.
# 2) Run this script from anywhere; it checks markers, syntax, and the diff.
# It never commits or pushes. If a marker is missing, do NOT commit yet.
$ErrorActionPreference = "Continue"
$root = "D:\Projects\بوت التليجرام\Telegram-shop"
Set-Location $root

function Test-Marker($file, $marker) {
    $p = Join-Path $root $file
    if (-not (Test-Path $p)) { Write-Host "MISSING FILE: $file"; return $false }
    $t = Get-Content $p -Raw -Encoding UTF8
    if ($t.Contains($marker)) { Write-Host "OK: $file -> $marker"; return $true }
    Write-Host "MISSING MARKER: $file -> $marker"; return $false
}

$ok = $true
$ok = (Test-Marker "subworld\button_models.py" "bot_buttons") -and $ok
$ok = (Test-Marker "subworld\button_models.py" "button_text_en") -and $ok
$ok = (Test-Marker "subworld\button_models.py" "full_width") -and $ok
$ok = (Test-Marker "subworld\buttons.py" "build_reply_kb") -and $ok
$ok = (Test-Marker "subworld\buttons.py" "ensure_default_buttons") -and $ok
$ok = (Test-Marker "subworld\buttons.py" "on_menu_button_text") -and $ok
$ok = (Test-Marker "subworld\db.py" "button_models") -and $ok
$ok = (Test-Marker "subworld\main.py" "send_main_menu") -and $ok
$ok = (Test-Marker "subworld\main.py" "on_menu_button_text") -and $ok
$ok = (Test-Marker "subworld\catalog.py" "send_store_sections") -and $ok
$ok = (Test-Marker "subworld\catalog.py" "send_balance") -and $ok
$ok = (Test-Marker "subworld\admin.py" "BotButtonAdmin") -and $ok
$ok = (Test-Marker "subworld\admin.py" "BotRuleAdmin") -and $ok

Write-Host "--- syntax ---"
python -m py_compile subworld\button_models.py subworld\buttons.py subworld\db.py subworld\main.py subworld\catalog.py subworld\admin.py
if ($LASTEXITCODE -eq 0) { Write-Host "py_compile OK" } else { Write-Host "py_compile FAILED"; $ok = $false }

Write-Host "--- diff ---"
git diff --stat

if ($ok) {
    Write-Host "All markers OK - commit + push when ready (this zip also includes the v6 catalog: pagination, categories, stock dots, move-to-Featured)."
} else {
    Write-Host "Some checks failed - do NOT commit yet, send the output back."
}
