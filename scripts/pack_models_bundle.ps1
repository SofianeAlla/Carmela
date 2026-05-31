# pack_models_bundle.ps1
#
# Developer-only. Run ONCE on the machine where you've already accepted
# the HuggingFace licenses and downloaded TRELLIS.2 + DINOv3 + RMBG-2.0
# into your local HF cache. Produces an archive set + manifest.json that
# you upload to your CDN; end users then point CARMELA_MODELS_BUNDLE_URL
# at the CDN base path and get a zero-friction install.
#
# Output: ./dist-bundle/
#   models.tar.zst                  (single-archive layout, simplest)
#   manifest.json                   (multi-archive layout, robust to partial fails)
#   trellis2-4b.tar.zst             when --Split is given
#   dinov3-vitl16.tar.zst
#   rmbg-2.tar.zst
#   trellis-image-large.tar.zst

param(
    [string]$OutDir = "dist-bundle",
    [switch]$Split,        # produce one archive per repo (recommended for CDN partial recovery)
    [int]$ZstdLevel = 19   # 1=fast, 22=max; 19 is the sweet spot for one-time pack
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$dist = Join-Path $root $OutDir
New-Item -ItemType Directory -Path $dist -Force | Out-Null

$hfCache = if ($env:HF_HOME) {
    Join-Path $env:HF_HOME "hub"
} else {
    Join-Path $env:USERPROFILE ".cache\huggingface\hub"
}
if (-not (Test-Path $hfCache)) {
    throw "HF cache not found at $hfCache. Run the dev installer first to download models."
}

# Repos we ship. If you add new ones, add them here too.
$repos = @(
    @{ id = "microsoft/TRELLIS.2-4B";                       dir = "models--microsoft--TRELLIS.2-4B";                       name = "trellis2-4b" },
    @{ id = "microsoft/TRELLIS-image-large";                dir = "models--microsoft--TRELLIS-image-large";                name = "trellis-image-large" },
    @{ id = "facebook/dinov3-vitl16-pretrain-lvd1689m";     dir = "models--facebook--dinov3-vitl16-pretrain-lvd1689m";     name = "dinov3-vitl16" },
    @{ id = "briaai/RMBG-2.0";                              dir = "models--briaai--RMBG-2.0";                              name = "rmbg-2" }
)

# Validate everything is present locally before we start packing
$missing = @()
foreach ($r in $repos) {
    $p = Join-Path $hfCache $r.dir
    if (-not (Test-Path $p) -or -not (Get-ChildItem -Path "$p\snapshots" -Recurse -File -ErrorAction SilentlyContinue)) {
        $missing += $r.id
    }
}
if ($missing.Count -gt 0) {
    Write-Host "Missing in HF cache:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "Accept licenses + huggingface-cli download these, then re-run." -ForegroundColor Yellow
    exit 1
}

function Sha256 {
    param([string]$Path)
    $h = Get-FileHash -Path $Path -Algorithm SHA256
    return $h.Hash.ToLower()
}

if ($Split) {
    $manifest = @{
        version = 1
        generator = "carmela/pack_models_bundle.ps1"
        files = @()
    }
    foreach ($r in $repos) {
        $arc = Join-Path $dist "$($r.name).tar.zst"
        Write-Host "[pack] $($r.id) -> $arc" -ForegroundColor Cyan
        # tar with -I "zstd -<N>" pipes through zstd. Windows tar supports this.
        Push-Location $hfCache
        & tar --use-compress-program="zstd -$ZstdLevel" -cf $arc $r.dir
        Pop-Location
        $size = (Get-Item $arc).Length
        $sha  = Sha256 $arc
        Write-Host ("  size: {0,8:F2} GB   sha256: {1}" -f ($size / 1GB), $sha)
        $manifest.files += @{
            repo = $r.id
            url  = "$($r.name).tar.zst"   # relative; CDN base is prepended by fetch script
            bytes = $size
            sha256 = $sha
        }
    }
    $manifestPath = Join-Path $dist "manifest.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -Path $manifestPath -Encoding utf8
    Write-Host ""
    Write-Host "[done] $($manifest.files.Count) archives + manifest.json in $dist" -ForegroundColor Green
} else {
    $arc = Join-Path $dist "models.tar.zst"
    Write-Host "[pack] single archive -> $arc" -ForegroundColor Cyan
    Push-Location $hfCache
    $dirArgs = $repos | ForEach-Object { $_.dir }
    & tar --use-compress-program="zstd -$ZstdLevel" -cf $arc @dirArgs
    Pop-Location
    $sz = (Get-Item $arc).Length / 1GB
    Write-Host ("[done] models.tar.zst {0:F2} GB" -f $sz) -ForegroundColor Green
}

Write-Host ""
Write-Host "Upload the contents of $dist to your CDN (e.g. s3://, R2://, or static host)." -ForegroundColor Yellow
Write-Host "Then set CARMELA_MODELS_BUNDLE_URL in the shipped .env / installer config:" -ForegroundColor Yellow
Write-Host "  CARMELA_MODELS_BUNDLE_URL=https://cdn.bespokeai.build/carmela/v1" -ForegroundColor DarkGray
