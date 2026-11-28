# Apply-SubWorld-Admin-Patch.ps1
# Mounts the SubWorld admin panel at /subworld in bot/web/admin.py (create_admin_app).
# - Only touches bot/web/admin.py, one insertion before "return app".
# - Idempotent: skips if the mount is already present.
# - Never closes the PowerShell window on a failed check (uses return, not exit).
# - Does NOT commit or push (that is a separate step).
# Run from the repo root:  .\Apply-SubWorld-Admin-Patch.ps1

$file = "bot/web/admin.py"

if (-not (Test-Path $file)) {
    Write-Host "ERROR: bot/web/admin.py not found. Run this script from the repo root." -ForegroundColor Red
    return
}
if (-not (Test-Path "subworld/admin.py")) {
    Write-Host "ERROR: subworld/admin.py not found. Extract the SubWorld package first." -ForegroundColor Red
    return
}

$text = [IO.File]::ReadAllText((Resolve-Path $file).Path)
$text = $text -replace "`r`n", "`n"

if ($text.Contains("create_subworld_admin_app")) {
    Write-Host "SubWorld admin mount already present - nothing to do." -ForegroundColor Green
    return
}

$anchor = "    admin.add_view(PaymentMethodView)`n`n    return app"
$anchorCount = ([regex]::Matches($text, [regex]::Escape($anchor))).Count
if ($anchorCount -ne 1) {
    Write-Host ("ERROR: mount anchor found {0} times (expected 1). File left unchanged." -f $anchorCount) -ForegroundColor Red
    return
}

$mountBlock = @'
    # SubWorld panel (separate database, same login): mounted at /subworld.
    # Guarded so a SubWorld problem can never break the Yemen panel.
    try:
        from subworld.admin import create_subworld_admin_app

        app.mount("/subworld", create_subworld_admin_app())
        logging.info("SubWorld admin panel mounted at /subworld")
    except Exception:
        logging.exception("SubWorld admin panel mount failed - Yemen panel unaffected")
'@
$mountBlock = $mountBlock.TrimEnd("`n")

$replacement = "    admin.add_view(PaymentMethodView)`n`n" + $mountBlock + "`n`n    return app"
$text = $text.Replace($anchor, $replacement)

[IO.File]::WriteAllText((Resolve-Path $file).Path, $text, (New-Object Text.UTF8Encoding($false)))
Write-Host "Patched bot/web/admin.py (SubWorld admin mount added)." -ForegroundColor Green

python -m py_compile $file
if ($LASTEXITCODE -eq 0) {
    Write-Host "py_compile OK" -ForegroundColor Green
} else {
    Write-Host "ERROR: py_compile failed - review the change before committing." -ForegroundColor Red
    return
}

git diff --stat
Write-Host "Next: review with git diff, then commit and push (separate step)." -ForegroundColor Cyan
