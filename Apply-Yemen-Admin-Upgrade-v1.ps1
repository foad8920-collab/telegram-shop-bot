# Yemen-Admin-Upgrade v1.0
# Upgrades sqladmin 0.16.1 -> 0.32.0 and converts all 20 plain-attribute
# column_filters (dead in 0.16.1, break list pages in 0.32) to explicit
# filter objects. Backs up BOTH touched files BEFORE any change.
#
# RUN WITH: pwsh (PowerShell 7). Do NOT run with Windows PowerShell 5.1
# (it mis-reads UTF-8 Arabic inside .ps1 files).
# Run from the project root (D:\Projects\بوت التليجرام\Telegram-shop)
# or pass -ProjectRoot explicitly.

param(
    [string]$ProjectRoot = (Get-Location).Path
)

Write-Host "=============================================="
Write-Host " Yemen-Admin-Upgrade v1.0"
Write-Host " sqladmin 0.16.1 -> 0.32.0  +  20 filters"
Write-Host "=============================================="

# --- 0. Must run under PowerShell 7+ ---
if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Host "STOP: this script must run with pwsh (PowerShell 7), not Windows PowerShell 5.1."
    Write-Host "Run: pwsh -File .\Apply-Yemen-Admin-Upgrade-v1.ps1"
    return
}

$adminPath = Join-Path $ProjectRoot "bot\web\admin.py"
$reqPath   = Join-Path $ProjectRoot "requirements.txt"

if (-not (Test-Path $adminPath)) { Write-Host "STOP: not found: $adminPath"; return }
if (-not (Test-Path $reqPath))   { Write-Host "STOP: not found: $reqPath"; return }

# --- 1. Strict whole-file UTF-8 validation for admin.py ---
try {
    $utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
    $adminBytes = [System.IO.File]::ReadAllBytes($adminPath)
    $null = $utf8Strict.GetString($adminBytes)
} catch {
    Write-Host "STOP: bot\web\admin.py is not valid UTF-8. Nothing was changed."
    return
}
$adminText = [System.IO.File]::ReadAllText($adminPath, $utf8Strict)

# --- 2. BACKUP first (before any change) ---
$backupDir = Join-Path $ProjectRoot "backup-2026-11-28-admin-upgrade-v1"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $adminPath (Join-Path $backupDir "admin.py") -Force
Copy-Item $reqPath   (Join-Path $backupDir "requirements.txt") -Force
Write-Host "Backup saved to: $backupDir"
Write-Host "  (restore: Copy-Item $backupDir\admin.py $adminPath -Force ; Copy-Item $backupDir\requirements.txt $reqPath -Force)"

# --- 3. Replace-Once helper: anchor must match EXACTLY once ---
function Replace-Once {
    param([ref]$Text, [string]$Old, [string]$New, [string]$Name)
    $count = ([regex]::Matches($Text.Value, [regex]::Escape($Old))).Count
    if ($count -ne 1) {
        Write-Host "STOP: anchor '$Name' matched $count times (expected 1). Nothing was written."
        return $false
    }
    $Text.Value = $Text.Value.Replace($Old, $New)
    Write-Host "  ok: $Name"
    return $true
}

$ok = $true

# 3a. import
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    'from sqladmin import Admin, BaseView, ModelView, expose, action' `
    @'
from sqladmin import Admin, BaseView, ModelView, expose, action
from sqladmin.filters import (
    AllUniqueStringValuesFilter,
    BooleanFilter,
    ForeignKeyFilter,
)
'@ `
    'sqladmin.filters import')

# 3b. UserActivityAdmin
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        UserActivity.action_type,
        UserActivity.target_type,
    ]
'@ `
    @'
    column_filters = [
        AllUniqueStringValuesFilter(UserActivity.action_type, title="الإجراء"),
        AllUniqueStringValuesFilter(UserActivity.target_type, title="نوع الهدف"),
    ]
'@ `
    'UserActivityAdmin filters')

# 3c. SupplierOrderAdmin
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        SupplierOrder.supplier_id,
        SupplierOrder.status,
    ]
'@ `
    @'
    column_filters = [
        ForeignKeyFilter(SupplierOrder.supplier_id, Supplier.name, title="المورد"),
        AllUniqueStringValuesFilter(SupplierOrder.status, title="الحالة"),
    ]
'@ `
    'SupplierOrderAdmin filters')

# 3d. SupplierSyncLogAdmin
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        SupplierSyncLog.supplier_id,
        SupplierSyncLog.status,
    ]
'@ `
    @'
    column_filters = [
        ForeignKeyFilter(SupplierSyncLog.supplier_id, Supplier.name, title="المورد"),
        AllUniqueStringValuesFilter(SupplierSyncLog.status, title="الحالة"),
    ]
'@ `
    'SupplierSyncLogAdmin filters')

# 3e. SupplierProductAdmin (the supplier dropdown)
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        SupplierProduct.supplier_id,
        SupplierProduct.category,
        SupplierProduct.is_visible,
        SupplierProduct.is_available,
        SupplierProduct.show_in_local,
    ]
'@ `
    @'
    column_filters = [
        ForeignKeyFilter(SupplierProduct.supplier_id, Supplier.name, title="المورد"),
        AllUniqueStringValuesFilter(SupplierProduct.category, title="الفئة"),
        BooleanFilter(SupplierProduct.is_visible, title="ظاهر"),
        BooleanFilter(SupplierProduct.is_available, title="متوفر"),
        BooleanFilter(SupplierProduct.show_in_local, title="في المحلية"),
    ]
'@ `
    'SupplierProductAdmin filters')

# 3f. SupplierView
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        Supplier.is_active,
        Supplier.last_sync_status,
    ]
'@ `
    @'
    column_filters = [
        BooleanFilter(Supplier.is_active, title="مفعّل"),
        AllUniqueStringValuesFilter(Supplier.last_sync_status, title="حالة المزامنة"),
    ]
'@ `
    'SupplierView filters')

# 3g. LocalSubscriptionView
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        LocalSubscription.delivery_type,
        LocalSubscription.category,
        LocalSubscription.is_active,
    ]
'@ `
    @'
    column_filters = [
        AllUniqueStringValuesFilter(LocalSubscription.delivery_type, title="نوع التسليم"),
        AllUniqueStringValuesFilter(LocalSubscription.category, title="الفئة"),
        BooleanFilter(LocalSubscription.is_active, title="مفعّل"),
    ]
'@ `
    'LocalSubscriptionView filters')

# 3h. LocalSubscriptionOrderAdmin
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        LocalSubscriptionOrder.status,
        LocalSubscriptionOrder.payment_type,
    ]
'@ `
    @'
    column_filters = [
        AllUniqueStringValuesFilter(LocalSubscriptionOrder.status, title="الحالة"),
        AllUniqueStringValuesFilter(LocalSubscriptionOrder.payment_type, title="طريقة الدفع"),
    ]
'@ `
    'LocalSubscriptionOrderAdmin filters')

# 3i. LocalSubscriptionCodeAdmin (relationship -> FK column)
$ok = $ok -and (Replace-Once ([ref]$adminText) `
    @'
    column_filters = [
        LocalSubscriptionCode.subscription,
        LocalSubscriptionCode.is_used,
    ]
'@ `
    @'
    column_filters = [
        ForeignKeyFilter(LocalSubscriptionCode.subscription_id, LocalSubscription.name, title="الاشتراك"),
        BooleanFilter(LocalSubscriptionCode.is_used, title="مُستخدَم"),
    ]
'@ `
    'LocalSubscriptionCodeAdmin filters')

if (-not $ok) {
    Write-Host "ABORTED: a check failed. admin.py was NOT written (backup already saved)."
    return
}

# --- 4. Write admin.py back (UTF-8, no BOM, LF preserved by .NET) ---
[System.IO.File]::WriteAllText($adminPath, $adminText, $utf8Strict)
Write-Host "Wrote: bot\web\admin.py"

# --- 5. requirements.txt: byte-level replace to preserve CRLF ---
$reqBytes = [System.IO.File]::ReadAllBytes($reqPath)
$oldB = [System.Text.Encoding]::ASCII.GetBytes("sqladmin==0.16.1")
$newB = [System.Text.Encoding]::ASCII.GetBytes("sqladmin==0.32.0")
$idx = -1
for ($i = 0; $i -le ($reqBytes.Length - $oldB.Length); $i++) {
    $match = $true
    for ($j = 0; $j -lt $oldB.Length; $j++) {
        if ($reqBytes[$i + $j] -ne $oldB[$j]) { $match = $false; break }
    }
    if ($match) { $idx = $i; break }
}
if ($idx -lt 0) { Write-Host "STOP: 'sqladmin==0.16.1' not found in requirements.txt. admin.py was already written - restore from backup if needed."; return }
$countOld = 0
for ($i = 0; $i -le ($reqBytes.Length - $oldB.Length); $i++) {
    $match = $true
    for ($j = 0; $j -lt $oldB.Length; $j++) {
        if ($reqBytes[$i + $j] -ne $oldB[$j]) { $match = $false; break }
    }
    if ($match) { $countOld++ }
}
if ($countOld -ne 1) { Write-Host "STOP: 'sqladmin==0.16.1' found $countOld times (expected 1)."; return }
$out = New-Object byte[] ($reqBytes.Length - $oldB.Length + $newB.Length)
[Array]::Copy($reqBytes, 0, $out, 0, $idx)
[Array]::Copy($newB, 0, $out, $idx, $newB.Length)
[Array]::Copy($reqBytes, $idx + $oldB.Length, $out, $idx + $newB.Length, $reqBytes.Length - $idx - $oldB.Length)
[System.IO.File]::WriteAllBytes($reqPath, $out)
Write-Host "Wrote: requirements.txt (sqladmin==0.16.1 -> sqladmin==0.32.0, CRLF kept)"

# --- 6. Post-checks on the written files ---
$after = [System.IO.File]::ReadAllText($adminPath, $utf8Strict)
$fk  = ([regex]::Matches($after, [regex]::Escape("ForeignKeyFilter("))).Count
$bl  = ([regex]::Matches($after, [regex]::Escape("BooleanFilter("))).Count
$au  = ([regex]::Matches($after, [regex]::Escape("AllUniqueStringValuesFilter("))).Count
$reqAfter = [System.IO.File]::ReadAllText($reqPath, [System.Text.Encoding]::ASCII)
Write-Host "Post-checks: ForeignKeyFilter x$fk (want 4) | BooleanFilter x$bl (want 6) | AllUniqueStringValuesFilter x$au (want 10)"
if ($fk -ne 4 -or $bl -ne 6 -or $au -ne 10) { Write-Host "STOP: post-check counts wrong. Restore from backup: $backupDir"; return }
if ($reqAfter -notmatch "sqladmin==0\.32\.0") { Write-Host "STOP: requirements.txt does not contain sqladmin==0.32.0."; return }
if ($after -match "column_filters = \[\s*\n\s*(UserActivity|SupplierOrder|SupplierSyncLog|SupplierProduct|Supplier|LocalSubscription)") {
    Write-Host "STOP: a plain-attribute filter block is still present."
    return
}
Write-Host ""
Write-Host "DONE. sqladmin 0.32.0 + 20 filters applied."
Write-Host "Backup: $backupDir"
Write-Host "Next: git add -A ; git commit -m 'sqladmin 0.32.0 + explicit filters' ; git push"
Write-Host "Then check Render deploy log, then open /admin/supplier-product/list and pick a supplier from the dropdown."
