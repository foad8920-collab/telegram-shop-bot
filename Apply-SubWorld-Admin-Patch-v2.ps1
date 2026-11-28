# Apply-SubWorld-Admin-Patch-v2.ps1
# Replaces the v1 app.mount() block in bot/web/admin.py with an ASGI
# dispatcher. Why: a plain mount makes SQLAdmin's url_for("admin:...")
# resolve to the Yemen panel (both panels register a route named "admin"
# and Starlette picks the first one), so every SubWorld link - login,
# logout, logo - landed on the Yemen panel. The dispatcher calls the
# SubWorld app directly, so its own router owns the "admin" name and all
# its links stay inside /subworld. Only root_path is extended; the path
# itself is left untouched, exactly like Mount does.
# - Only touches bot/web/admin.py, one block replaced.
# - Idempotent: skips if the dispatcher is already present.
# - Never closes the PowerShell window on a failed check (uses return, not exit).
# - Does NOT commit or push (that is a separate step).
# Run from the repo root:  .\Apply-SubWorld-Admin-Patch-v2.ps1

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

if ($text.Contains("_SubWorldDispatcher")) {
    Write-Host "SubWorld dispatcher already present - nothing to do." -ForegroundColor Green
    return
}

$anchor = @'
    # SubWorld panel (separate database, same login): mounted at /subworld.
    # Guarded so a SubWorld problem can never break the Yemen panel.
    try:
        from subworld.admin import create_subworld_admin_app

        app.mount("/subworld", create_subworld_admin_app())
        logging.info("SubWorld admin panel mounted at /subworld")
    except Exception:
        logging.exception("SubWorld admin panel mount failed - Yemen panel unaffected")
'@
$anchor = $anchor.Trim("`n")
$anchorCount = ([regex]::Matches($text, [regex]::Escape($anchor))).Count
if ($anchorCount -ne 1) {
    Write-Host ("ERROR: v1 mount block found {0} times (expected 1). File left unchanged." -f $anchorCount) -ForegroundColor Red
    return
}

$replacement = @'
    # SubWorld panel (separate database, same login): served at /subworld
    # through a small ASGI dispatcher, NOT app.mount(). A plain mount makes
    # SQLAdmin's url_for("admin:...") resolve to the Yemen panel: both
    # panels register a route named "admin" and Starlette picks the first
    # one, so every SubWorld link (login, logout, logo) lands on Yemen.
    # The dispatcher calls the SubWorld app directly, so its own router
    # owns the "admin" name and all its links stay inside /subworld.
    # Only root_path is extended; path/raw_path are left untouched,
    # exactly like Mount does (stripping the path breaks matching).
    # Guarded so a SubWorld problem can never break the Yemen panel.
    try:
        from subworld.admin import create_subworld_admin_app

        _subworld_app = create_subworld_admin_app()

        class _SubWorldDispatcher:
            """Route /subworld/* to the SubWorld app, everything else through."""

            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http" and scope["path"].startswith("/subworld"):
                    sub_scope = dict(scope)
                    sub_scope["root_path"] = scope.get("root_path", "") + "/subworld"
                    await _subworld_app(sub_scope, receive, send)
                    return
                await self.app(scope, receive, send)

        app = _SubWorldDispatcher(app)
        logging.info("SubWorld admin panel served at /subworld")
    except Exception:
        logging.exception("SubWorld admin panel mount failed - Yemen panel unaffected")
'@
$replacement = $replacement.Trim("`n")

$text = $text.Replace($anchor, $replacement)

[IO.File]::WriteAllText((Resolve-Path $file).Path, $text, (New-Object Text.UTF8Encoding($false)))
Write-Host "Patched bot/web/admin.py (SubWorld dispatcher replaces app.mount)." -ForegroundColor Green

python -m py_compile $file
if ($LASTEXITCODE -eq 0) {
    Write-Host "py_compile OK" -ForegroundColor Green
} else {
    Write-Host "ERROR: py_compile failed - review the change before committing." -ForegroundColor Red
    return
}

git diff --stat
Write-Host "Next: review with git diff, then commit and push (separate step)." -ForegroundColor Cyan
