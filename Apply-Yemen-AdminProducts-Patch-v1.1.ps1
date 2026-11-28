# Apply-Yemen-AdminProducts-Patch-v1.1.ps1  v1.1 (HOTFIX)
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-AdminProducts-Patch-v1.1.ps1
#
# HOTFIX for the v1 deploy crash (2026-11-28 09:19 UTC):
#   ModuleNotFoundError: No module named 'sqladmin.filters'
# The project pins sqladmin==0.16.1, and that version has NO
# sqladmin.filters module and NO column_filters feature at all, so the
# ForeignKeyFilter import killed the whole bot at startup. This patch
# removes the bad import and the ForeignKeyFilter (back to the plain
# supplier_id filter, which the old version simply ignores) and KEEPS
# the column-order change (show_in_local + is_available before
# cost_price), which does not depend on the SQLAdmin version.
# The supplier-name dropdown needs a different approach for 0.16.1
# and will come as a separate patch - the bot comes first.
#
# The zip contains ONLY this script (no bot files), so extracting it
# cannot overwrite anything. The script patches the live file in place
# and backs it up first.
#
# Writes NOTHING if any anchor check fails. All edits are in-memory
# until every check passes, then written once.
$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Yemen-AdminProducts-Patch v1.1 (hotfix)"

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
$admin = Join-Path $root "bot\web\admin.py"
if (-not (Test-Path $admin)) { Write-Host "STOP: not found: $admin"; return }
if (-not (Assert-Utf8 $admin "admin-utf8")) { return }

# ---- Backup ----
$stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
$backupDir = Join-Path $root "backup-$stamp-admin-products-v11"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $admin (Join-Path $backupDir "admin.py")
Write-Host "Backup: $backupDir"

# ============ 1. Remove the bad import (it does not exist in sqladmin 0.16.1) ============
Replace-Anchor -Path $admin -Label "remove-fk-import" -Anchor @'
from sqladmin import Admin, BaseView, ModelView, expose, action
from sqladmin.filters import ForeignKeyFilter
'@ -New @'
from sqladmin import Admin, BaseView, ModelView, expose, action
'@

# ============ 2. Revert the supplier filter to the plain column (0.16.1 ignores it) ============
Replace-Anchor -Path $admin -Label "revert-supplier-filter" -Anchor @'
    column_filters = [
        ForeignKeyFilter(SupplierProduct.supplier_id, Supplier.name, title="المورد"),
        SupplierProduct.category,
        SupplierProduct.is_visible,
        SupplierProduct.is_available,
        SupplierProduct.show_in_local,
    ]
'@ -New @'
    column_filters = [
        SupplierProduct.supplier_id,
        SupplierProduct.category,
        SupplierProduct.is_visible,
        SupplierProduct.is_available,
        SupplierProduct.show_in_local,
    ]
'@

if ($script:failed.Count -gt 0) {
    Write-Host "FAILED checks: $($script:failed -join ', ') - nothing was written."
    return
}

# ---- Apply all edits (in memory), then write the file once ----
$grouped = $script:edits | Group-Object Path
foreach ($g in $grouped) {
    $text = Get-Text $g.Name
    foreach ($e in $g.Group) {
        $text = $text.Replace($e.Anchor, $e.New)
    }
    Set-Text $g.Name $text
}
Write-Host "Done. File changed: bot/web/admin.py"
Write-Host "The column-order change (local/available before cost price) is KEPT."
Write-Host "Next: py_compile the file, then git add/commit/push - the bot will start again."
