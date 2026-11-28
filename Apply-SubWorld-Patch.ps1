# Apply-SubWorld-Patch.ps1
# يعدل run.py فقط: يضيف بوت SubWorld (البوت الرئيسي) كأول مهمة في gather.
# لا يغلق النافذة عند الخطأ، ولا ينشر شيئا. النشر خطوة منفصلة بعد مراجعة الفرق.
# آمن لإعادة التشغيل: إن كان التعديل مطبقا أصلا يتخطاه ويكتفي بالفحص.
$ErrorActionPreference = "Continue"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunPy = Join-Path $RepoRoot "run.py"
$SubworldMain = Join-Path $RepoRoot "subworld\main.py"
$SubworldInit = Join-Path $RepoRoot "subworld\__init__.py"

Write-Host "Repo: $RepoRoot"

$ok = $true
if (-not (Test-Path $RunPy)) { Write-Host "ERROR: run.py غير موجود: $RunPy"; $ok = $false }
if (-not (Test-Path $SubworldMain)) { Write-Host "ERROR: subworld\main.py غير موجود - فك ضغط الحزمة داخل مجلد المشروع أولا"; $ok = $false }
if (-not (Test-Path $SubworldInit)) { Write-Host "ERROR: subworld\__init__.py غير موجود - فك ضغط الحزمة داخل مجلد المشروع أولا"; $ok = $false }

if ($ok) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $text = [System.IO.File]::ReadAllText($RunPy, [System.Text.Encoding]::UTF8)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    $text = $text -replace "`r`n", "`n"

    if ($text.Contains("_run_subworld_bot")) {
        Write-Host "Patch already applied - skipping edit, running checks only."
    } else {
        $anchorMain = "async def main():"
        $anchorGather = "    await asyncio.gather(`n        _run_shop_bot(),`n        _run_support_bot(),`n    )"
        $anchorDoc = '"""تشغيل البوتين معًا بشكل متوازي."""'
        $anchorLog = 'logger.info("Starting shop bot and support bot together...")'

        $countMain = ([regex]::Matches($text, [regex]::Escape($anchorMain))).Count
        $countGather = ([regex]::Matches($text, [regex]::Escape($anchorGather))).Count
        $countDoc = ([regex]::Matches($text, [regex]::Escape($anchorDoc))).Count
        $countLog = ([regex]::Matches($text, [regex]::Escape($anchorLog))).Count
        Write-Host "Anchors: main=$countMain gather=$countGather doc=$countDoc log=$countLog (expected 1 each)"

        if ($countMain -ne 1 -or $countGather -ne 1 -or $countDoc -ne 1 -or $countLog -ne 1) {
            Write-Host "ERROR: anchor count mismatch - file not changed. Send this output and do NOT rerun before inspection."
            $ok = $false
        } else {
            $newFunc = @'
async def _run_subworld_bot():
    """تشغيل بوت SubWorld (البوت الرئيسي)."""
    try:
        from subworld import main as subworld_main
        await subworld_main()
    except Exception as e:
        logger.exception("SubWorld bot failed: %s", e)


'@
            $newGather = "    await asyncio.gather(`n        _run_subworld_bot(),`n        _run_shop_bot(),`n        _run_support_bot(),`n    )"
            $newDoc = '"""تشغيل البوتات معًا بشكل متوازي (SubWorld هو البوت الرئيسي)."""'
            $newLog = 'logger.info("Starting bots together (SubWorld is the main bot)...")'

            $text = $text.Replace($anchorMain, $newFunc + $anchorMain)
            $text = $text.Replace($anchorGather, $newGather)
            $text = $text.Replace($anchorDoc, $newDoc)
            $text = $text.Replace($anchorLog, $newLog)

            [System.IO.File]::WriteAllText($RunPy, $text, $utf8NoBom)
            Write-Host "run.py patched: SubWorld added first in gather."
        }
    }

    if ($ok) {
        try {
            & python -m py_compile run.py subworld\main.py subworld\__init__.py
            if ($LASTEXITCODE -eq 0) { Write-Host "py_compile OK" } else { Write-Host "ERROR: py_compile failed (exit $LASTEXITCODE) - syntax only, do not publish." }
        } catch {
            Write-Host "WARNING: could not run python py_compile: $_"
        }
        try {
            Write-Host "--- git diff --stat ---"
            & git diff --stat
            Write-Host "--- git status ---"
            & git status --porcelain
        } catch {
            Write-Host "WARNING: could not run git: $_"
        }
        Write-Host "Review the diff with: git diff run.py"
        Write-Host "Publish only after review, as a separate step: git add run.py subworld ; git commit ; git push"
    }
}

Write-Host "Done."
