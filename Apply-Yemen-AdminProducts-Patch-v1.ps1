# Apply-Yemen-AdminProducts-Patch-v1.ps1  v1.0
# RUN WITH pwsh (PowerShell 7), NOT powershell 5.1:
#   pwsh -ExecutionPolicy Bypass -File .\Apply-Yemen-AdminProducts-Patch-v1.ps1
#
# Admin panel - supplier products list (SupplierProductAdmin):
#   1. A real supplier dropdown at the top. The old filter was the bare
#      supplier_id column, which SQLAdmin does not render as a dropdown
#      with names. It is replaced by an explicit ForeignKeyFilter that
#      lists every supplier by name (StackVault, Pandora, ...) - pick
#      one and only that supplier's products are shown.
#   2. Column order: "في المحلية" (show_in_local) and "متوفر"
#      (is_available) move to just before "سعر التكلفة" (cost_price).
#
# The zip for this patch contains ONLY this script (no bot files), so
# extracting it cannot overwrite anything. The script patches the
# live file in place and backs it up first.
#
# Writes NOTHING if any anchor check fails. All edits are in-memory
# until every check passes, then written once.
$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Write-Host "Yemen-AdminProducts-Patch v1.0"

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
$backupDir = Join-Path $root "backup-$stamp-admin-products"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $admin (Join-Path $backupDir "admin.py")
Write-Host "Backup: $backupDir"

# ============ 1. Import ForeignKeyFilter ============
Replace-Anchor -Path $admin -Label "import-fk-filter" -Anchor @'
from sqladmin import Admin, BaseView, ModelView, expose, action
'@ -New @'
from sqladmin import Admin, BaseView, ModelView, expose, action
from sqladmin.filters import ForeignKeyFilter
'@

# ============ 2. Column order: show_in_local + is_available before cost_price ============
Replace-Anchor -Path $admin -Label "column-order" -Anchor @'
    column_list = [
        SupplierProduct.id,
        SupplierProduct.supplier_id,
        SupplierProduct.name,
        SupplierProduct.category,
        SupplierProduct.cost_price,
        SupplierProduct.fixed_profit,
        SupplierProduct.selling_price,
        SupplierProduct.manual_price,
        SupplierProduct.stock,
        SupplierProduct.is_visible,
        SupplierProduct.show_in_local,
        SupplierProduct.is_available,
        SupplierProduct.last_synced_at,
    ]
'@ -New @'
    column_list = [
        SupplierProduct.id,
        SupplierProduct.supplier_id,
        SupplierProduct.name,
        SupplierProduct.category,
        SupplierProduct.show_in_local,
        SupplierProduct.is_available,
        SupplierProduct.cost_price,
        SupplierProduct.fixed_profit,
        SupplierProduct.selling_price,
        SupplierProduct.manual_price,
        SupplierProduct.stock,
        SupplierProduct.is_visible,
        SupplierProduct.last_synced_at,
    ]
'@

# ============ 3. Supplier dropdown filter (names, not IDs) ============
Replace-Anchor -Path $admin -Label "supplier-dropdown" -Anchor @'
    column_filters = [
        SupplierProduct.supplier_id,
        SupplierProduct.category,
        SupplierProduct.is_visible,
        SupplierProduct.is_available,
        SupplierProduct.show_in_local,
    ]
'@ -New @'
    column_filters = [
        ForeignKeyFilter(SupplierProduct.supplier_id, Supplier.name, title="المورد"),
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
Write-Host "Next: py_compile the file, then git add/commit/push."
Write-Host "After deploy, open the supplier products list: a 'المورد' dropdown sits above the table."
