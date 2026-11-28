# Verify the PgBouncer fix in subworld/db.py (v4) - no file edits here.
# Run after extracting subworld-admin-v4.zip at the repo root:
#   cd "D:\Projects\بوت التليجرام\Telegram-shop"
#   Expand-Archive -Path "C:\Users\AL-FOAD\Documents\Downloads\subworld-admin-v4.zip" -DestinationPath . -Force
#   .\Apply-SubWorld-Admin-Patch-v3.ps1
# Then commit + push (separate step).

$ErrorActionPreference = "Stop"

$dbFile = "subworld\db.py"
if (-not (Test-Path $dbFile)) {
    Write-Host "MISSING: $dbFile - extract the zip at the repo root first."
    return
}

$content = Get-Content $dbFile -Raw -Encoding UTF8
$checks = @(
    "prepared_statement_name_func",
    "NullPool",
    "uuid4"
)
$missing = @()
foreach ($m in $checks) {
    if ($content -notmatch [regex]::Escape($m)) { $missing += $m }
}
if ($missing.Count -gt 0) {
    Write-Host ("NOT APPLIED: missing markers in {0}: {1} - re-extract the zip with -Force." -f $dbFile, ($missing -join ", "))
    return
}
Write-Host "Markers OK in $dbFile (prepared_statement_name_func + NullPool + uuid4)."

python -m py_compile $dbFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "py_compile FAILED - do not commit."
    return
}
Write-Host "py_compile OK"

git diff --stat
Write-Host "Next: review with git diff, then commit and push (separate step)."
