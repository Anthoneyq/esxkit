# collect_xm8.ps1 — read-only collector for Xactimate Desktop local artifacts.
# Run on the licensed user's own machine (no admin needed for ProgramData).
# Collects ONLY the artifacts documented in Research/notes/07-local-data-map.md.
# Excludes: the 346MB update zip, RenderAssets, .enc files, image caches.
# Output: Desktop\esxkit_collect_<timestamp>.zip + manifest.csv (sha256 per file)
#
# NOTE: output contains claim documents (real ESX files). Share only with
# parties who need them.

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$root  = Join-Path $env:ProgramData 'Xactware\XactimateDesktop'
$work  = Join-Path $env:TEMP "esxkit_collect_$stamp"
$dest  = Join-Path ([Environment]::GetFolderPath('Desktop')) "esxkit_collect_$stamp"
New-Item -ItemType Directory -Force -Path $work, "$work\projrecycle", "$work\estimates", "$work\data", "$work\catalog", "$work\config" | Out-Null

$manifest = @()

function Get-Item2($path, $targetDir) {
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination $targetDir -Force
        $f = Get-Item -LiteralPath $path
        $h = Get-FileHash -LiteralPath $path -Algorithm SHA256
        $script:manifest += [pscustomobject]@{
            Source = $path; Bytes = $f.Length; SHA256 = $h.Hash }
        Write-Host ("  collected: " + $f.Name)
    } else {
        Write-Host ("  missing:   " + $path)
    }
}

Write-Host "1/5 ProjRecycle ESX + XML sidecars..."
Get-ChildItem -LiteralPath (Join-Path $root 'xm8\ProjRecycle') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in '.ESX', '.esx', '.xml' } |
    ForEach-Object { Get-Item2 $_.FullName "$work\projrecycle" }

Write-Host "2/5 Live project doc.zip stores..."
Get-ChildItem -LiteralPath (Join-Path $root 'xm8\data\estimates') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like '*.doc.zip' } |
    ForEach-Object { Get-Item2 $_.FullName "$work\estimates" }

Write-Host "3/5 Local databases (small) + search index..."
foreach ($p in @(
    (Join-Path $root 'WinDb.sqlite'),
    (Join-Path $root 'analytics\analytics.sqlite'),
    (Join-Path $root 'xactimate.db3'),
    (Join-Path $root 'xm8\data\estimate_queue_results.sqlite'),
    (Join-Path $root 'xm8\data\estimate_queue_results.sqlite-wal'),
    (Join-Path $root 'xm8\data\estimate_queue_results.sqlite-shm'),
    (Join-Path $root 'xm8\data\queues.sqlite'),
    (Join-Path $root 'xm8\data\queues.sqlite-wal'),
    (Join-Path $root 'xm8\data\queues.sqlite-shm'),
    (Join-Path $root 'xm8\data\PLSearchIndex\SynonymList_en-US')
)) { Get-Item2 $p "$work\data" }

Write-Host "4/5 ItemHierarchy catalog zips (LOCAL USE ONLY - do not redistribute)..."
Get-ChildItem -LiteralPath (Join-Path $root 'core\data\ItemHierarchy') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -eq '.zip' } |
    ForEach-Object { Get-Item2 $_.FullName "$work\catalog" }

Write-Host "5/5 Config files..."
foreach ($p in @(
    (Join-Path $root 'core\xm8config.xml'),
    (Join-Path $root 'MachineConfig.json'),
    (Join-Path $root 'FT.ini')
)) { Get-Item2 $p "$work\config" }

$manifest | Export-Csv -NoTypeInformation -Path (Join-Path $work 'manifest.csv')
Compress-Archive -Path (Join-Path $work '*') -DestinationPath ($dest + '.zip') -Force
Write-Host ""
Write-Host ("DONE: " + $dest + ".zip")
Write-Host ("Contains claim documents. Share only with parties who need them.")
