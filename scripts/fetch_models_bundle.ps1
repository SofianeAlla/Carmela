# fetch_models_bundle.ps1
#
# End-user model fetcher. Reads CARMELA_MODELS_BUNDLE_URL from the
# environment (or .env in the project root) and downloads the prebuilt
# Carmela model pack into the user's HuggingFace cache directory.
# When the bundle is in place, no HF auth, no license click-through, no
# WinError 10054 retry loop — just one curl + extract.
#
# Bundle format (the dev-side packager produces this):
#   <url>/manifest.json            Lists files + sha256.
#   <url>/models-v<N>.tar.zst      Single archive, ~13 GB compressed.
# OR
#   <url>/trellis2-4b.tar.zst
#   <url>/dinov3-vitl16.tar.zst
#   <url>/rmbg-2.tar.zst
#   <url>/trellis-image-large.tar.zst
# The script handles both layouts: if manifest.json exists, follow it;
# otherwise grab a single models.tar.zst.

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $root

$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$' -and -not ($_ -match '^\s*#')) {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
        }
    }
}

$bundleUrl = $env:CARMELA_MODELS_BUNDLE_URL
if ([string]::IsNullOrWhiteSpace($bundleUrl)) {
    Write-Host "[bundle] CARMELA_MODELS_BUNDLE_URL not set. Skipping bundle fetch." -ForegroundColor Yellow
    Write-Host "         Set it in .env to enable zero-friction installs:" -ForegroundColor DarkGray
    Write-Host "           CARMELA_MODELS_BUNDLE_URL=https://cdn.bespokeai.build/carmela/models-v1/" -ForegroundColor DarkGray
    Write-Host "         End users will then download weights from your CDN" -ForegroundColor DarkGray
    Write-Host "         instead of HuggingFace (no auth, no licenses)." -ForegroundColor DarkGray
    Pop-Location
    exit 0
}
$bundleUrl = $bundleUrl.TrimEnd('/')

$hfCacheRoot = if ($env:HF_HOME) {
    Join-Path $env:HF_HOME "hub"
} else {
    Join-Path $env:USERPROFILE ".cache\huggingface\hub"
}
New-Item -ItemType Directory -Path $hfCacheRoot -Force | Out-Null
$work = Join-Path $hfCacheRoot "_carmela_bundle"
New-Item -ItemType Directory -Path $work -Force | Out-Null

function Get-File {
    param([string]$Url, [string]$Out)
    Write-Host "[get] $Url" -ForegroundColor Cyan
    # curl is fast + has resume; uses ~3 GB chunk window so RST events recover
    & curl -fL --retry 12 --retry-delay 5 --retry-all-errors -C - -o $Out $Url
    return $LASTEXITCODE
}

function Expand-Archive-Auto {
    param([string]$Archive, [string]$Dest)
    Write-Host "[extract] $Archive -> $Dest" -ForegroundColor Cyan
    # Windows ships tar.exe since Win10 1803. Handles .tar / .tar.gz / .tar.zst (10+).
    & tar -xf $Archive -C $Dest
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] extraction failed; tar exit=$LASTEXITCODE" -ForegroundColor Red
        return $false
    }
    return $true
}

# 1. Try manifest.json first
$manifestUrl = "$bundleUrl/manifest.json"
$manifestPath = Join-Path $work "manifest.json"
Get-File $manifestUrl $manifestPath | Out-Null

if (Test-Path $manifestPath -and (Get-Item $manifestPath).Length -gt 10) {
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    Write-Host "[bundle] manifest v$($manifest.version) — $($manifest.files.Count) archive(s)" -ForegroundColor Green
    $allOk = $true
    foreach ($f in $manifest.files) {
        $localArc = Join-Path $work (Split-Path $f.url -Leaf)
        if (Get-File ($f.url) $localArc -ne 0) { $allOk = $false; continue }
        if (-not (Expand-Archive-Auto $localArc $hfCacheRoot)) { $allOk = $false }
    }
    if ($allOk) { Write-Host "[OK] all model archives extracted" -ForegroundColor Green }
} else {
    # 2. Single-archive layout
    $singleUrl = "$bundleUrl/models.tar.zst"
    $localArc = Join-Path $work "models.tar.zst"
    if (Get-File $singleUrl $localArc -eq 0) {
        Expand-Archive-Auto $localArc $hfCacheRoot | Out-Null
    } else {
        Write-Host "[FAIL] no manifest.json and no models.tar.zst at $bundleUrl" -ForegroundColor Red
    }
}

# Clean up the working dir; keep the cache.
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

# Quick verification — show which repos landed
Write-Host ""
Write-Host "[verify] HuggingFace cache contents:" -ForegroundColor Cyan
Get-ChildItem $hfCacheRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "models--*" } | ForEach-Object {
    $sz = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB
    Write-Host ("  {0,-65} {1,8:F2} GB" -f $_.Name, $sz)
}
Pop-Location
