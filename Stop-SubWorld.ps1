# Stop-SubWorld.ps1  v1.1
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Stop-SubWorld.ps1
# v1.1: the UTF-8 guard now validates the whole file as strict UTF-8
# (v1.0 wrongly looked for the word "صورة", which run.py never contains).
#
# STAGE 1 of retiring SubWorld (merge into the Yemen bot): STOP it, do not
# delete it. Fully reversible - the code, the token and the database all
# stay where they are; only the polling task and the /subworld panel are
# switched off.
#
# Files touched:
#   run.py            - _run_subworld_bot() removed from the gather only.
#                       The function stays, so re-enabling is one line.
#                       The docstring and log line go back to two bots.
#   bot/web/admin.py  - the _SubWorldDispatcher block is commented out
#                       (kept in place, so uncommenting restores it).
#                       /subworld then returns the Yemen app's 404.
# Nothing else is touched: the subworld/ folder, the Yemen bot, the
# panel login and the Render service all stay as they are.
#
# Safe: every edit is anchor-checked (exact count) before any write, the
# script is idempotent - re-running it skips what is already stopped - and
# it never calls exit; on a failed anchor it reports the problem and
# returns without writing that file. A backup of both files is saved
# first (restore commands are printed at the end of the run).

$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Stop-SubWorld v1.1"

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
    # True only if the whole file decodes as valid UTF-8. (The old probe
    # looked for the word "صورة", which run.py and admin.py never contain,
    # so it wrongly failed on perfectly good files. Fixed in v1.1.)
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
$backupDir = "backup-2026-11-28-stop-subworld"
if (Test-Path $backupDir) { $backupDir = "backup-2026-11-28-stop-subworld-v2" }
$backupFiles = @("run.py", "bot\web\admin.py")
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $backupFiles) {
    if (-not (Test-Path $f)) { Write-Host "MISSING FILE: $f - run from the repo root."; return }
    Copy-Item $f "$backupDir\" -Force
}
Write-Host "Backup saved in $backupDir."
Write-Host "To restore (re-enable SubWorld): Copy-Item $backupDir\run.py . -Force; Copy-Item $backupDir\admin.py bot\web\ -Force; git add run.py bot; git commit -m 'restore SubWorld'; git push"

foreach ($f in $backupFiles) {
    if (-not (Test-Utf8 $f)) {
        Write-Host "STOP: $f is not valid UTF-8. Re-save it as UTF-8 in your editor, then re-run. Nothing was changed."
        return
    }
}

# ---------------- 1) run.py: drop SubWorld from the gather ----------------
$runPath = "run.py"
$run = Read-Text $runPath
$runText = $run.Text

if (-not $runText.Contains("_run_subworld_bot")) {
    Write-Host "run.py: no SubWorld task - nothing to stop here."
} else {
    $gOld = @'
    await asyncio.gather(
        _run_subworld_bot(),
        _run_shop_bot(),
        _run_support_bot(),
    )
'@
    $gNew = @'
    # SubWorld stopped 2026-11-28 (merge into the Yemen bot).
    # To re-enable: add _run_subworld_bot() back into the gather below.
    await asyncio.gather(
        _run_shop_bot(),
        _run_support_bot(),
    )
'@
    if ($runText.Contains("_run_subworld_bot(),")) {
        if ((Count-Anchor $runText $gOld) -ne 1) {
            Write-Host "ANCHOR FAILED: run.py gather (found $((Count-Anchor $runText $gOld)), need 1). Nothing written to $runPath."
            return
        }
        $runText = $runText.Replace($gOld, $gNew)
        Write-Host "run.py: SubWorld removed from the gather."
    } else {
        Write-Host "run.py: gather already has no SubWorld task - skipped."
    }

    $dOld = '"""تشغيل البوتات معًا بشكل متوازي (SubWorld هو البوت الرئيسي)."""'
    $dNew = '"""تشغيل البوتين معًا بشكل متوازي."""'
    if ($runText.Contains($dOld)) {
        if ((Count-Anchor $runText $dOld) -ne 1) {
            Write-Host "ANCHOR FAILED: run.py docstring (found $((Count-Anchor $runText $dOld)), need 1). Nothing written to $runPath."
            return
        }
        $runText = $runText.Replace($dOld, $dNew)
        Write-Host "run.py: docstring back to two bots."
    } else {
        Write-Host "run.py: docstring already two bots - skipped."
    }

    $lOld = 'logger.info("Starting bots together (SubWorld is the main bot)...")'
    $lNew = 'logger.info("Starting shop bot and support bot together...")'
    if ($runText.Contains($lOld)) {
        if ((Count-Anchor $runText $lOld) -ne 1) {
            Write-Host "ANCHOR FAILED: run.py log line (found $((Count-Anchor $runText $lOld)), need 1). Nothing written to $runPath."
            return
        }
        $runText = $runText.Replace($lOld, $lNew)
        Write-Host "run.py: log line back to two bots."
    } else {
        Write-Host "run.py: log line already two bots - skipped."
    }

    Write-Text $runPath $runText $run.Crlf
    Write-Host "run.py: written."
}

python -m py_compile run.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "py_compile FAILED for run.py - review the output above."
    return
}
Write-Host "run.py: compiles."

# ---------------- 2) bot/web/admin.py: comment out the dispatcher ----------------
$admPath = "bot\web\admin.py"
$adm = Read-Text $admPath
$admText = $adm.Text

if ($admText.Contains("# SubWorld panel DISABLED 2026-11-28")) {
    Write-Host "admin.py: SubWorld panel already disabled - skipped."
} elseif (-not $admText.Contains("`n        app = _SubWorldDispatcher(app)")) {
    Write-Host "admin.py: no active SubWorld dispatcher - nothing to stop here."
} else {
    $aOld = @'
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
    if ((Count-Anchor $admText $aOld) -ne 1) {
        Write-Host "ANCHOR FAILED: admin.py dispatcher block (found $((Count-Anchor $admText $aOld)), need 1). Nothing written to $admPath."
        return
    }
    $lines = $aOld -split "`n"
    $commented = ($lines | ForEach-Object { if ($_ -match "^\s*$") { $_ } else { "# " + $_ } }) -join "`n"
    $header = "    # SubWorld panel DISABLED 2026-11-28 (merge into the Yemen bot). Uncomment the block below to re-enable."
    $admText = $admText.Replace($aOld, $header + "`n" + $commented)
    Write-Text $admPath $admText $adm.Crlf
    Write-Host "admin.py: dispatcher commented out."
}

python -m py_compile bot\web\admin.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "py_compile FAILED for bot/web/admin.py - review the output above."
    return
}
Write-Host "admin.py: compiles."
Write-Host "OK: SubWorld stopped. The Yemen bot, the panel login and the subworld/ folder are untouched."
