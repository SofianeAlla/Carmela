# CARMELA — first-run installer.
# Sets up the Python sidecar venv, Electron deps, and (by default) TRELLIS 2.0
# locally — including its native CUDA extensions.
#
# Usage:
#   .\scripts\install.ps1                    # full install (recommended)
#   .\scripts\install.ps1 -SkipTrellis       # cloud-only: BespokeAI API, no local TRELLIS
#   .\scripts\install.ps1 -Yes               # non-interactive: assume yes to all prompts

param(
    [switch]$SkipTrellis,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $root

function Write-Step {
    param([string]$Msg, [string]$Color = "Cyan")
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor $Color
}

function Ask-YesNo {
    param([string]$Prompt, [bool]$Default = $true)
    if ($Yes) { return $true }
    $suffix = if ($Default) { "[Y/n]" } else { "[y/N]" }
    $resp = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($resp)) { return $Default }
    return $resp -match '^[Yy]'
}

Write-Host ""
Write-Host "  ▄████▄   ▄▄▄       ██▀███   ███▄ ▄███▓▓█████  ██▓    ▄▄▄      " -ForegroundColor DarkYellow
Write-Host "  ██▀ ▀█  ▒████▄    ▓██ ▒ ██▒▓██▒▀█▀ ██▒▓█   ▀ ▓██▒   ▒████▄    " -ForegroundColor DarkYellow
Write-Host "  ██▄▄▄  ▒██  ▀█▄  ▓██ ░▄█ ▒▓██    ▓██░▒███   ▒██░   ▒██  ▀█▄  " -ForegroundColor DarkYellow
Write-Host "  3D AI Asset Generation for CARLA Simulator" -ForegroundColor Gray
Write-Host ""

# ---------- 1. core deps (Python sidecar + Electron) ----------

Write-Step "[1/4] Python sidecar venv (3.10)"
if (-not (Test-Path ".venv")) {
    uv venv --python 3.10 .venv
}
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
uv pip install --python .venv\Scripts\python.exe carla==0.9.16

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "  Created .env - paste your BespokeAI key into Settings after launch." -ForegroundColor Yellow
}

Write-Step "[2/4] Electron desktop deps"
Push-Location desktop
npm install --no-audit --no-fund
Pop-Location

# ---------- 2. TRELLIS 2.0 (highly recommended) ----------

$installTrellis = $false
if (-not $SkipTrellis) {
    Write-Step "[3/4] TRELLIS 2.0 (highly recommended)" "Yellow"
    Write-Host "  TRELLIS 2.0 is Microsoft's image-to-3D model. Installing it locally lets" -ForegroundColor Gray
    Write-Host "  CARMELA generate without a cloud round-trip. Requirements (auto-checked):" -ForegroundColor Gray
    Write-Host "    - NVIDIA GPU (8 GB+ VRAM, fp16 + offload for <24 GB)" -ForegroundColor Gray
    Write-Host "    - CUDA Toolkit 12.4 (nvcc)" -ForegroundColor Gray
    Write-Host "    - Visual Studio 2022 Build Tools with MSVC v14.40 toolset (CUDA-compat)" -ForegroundColor Gray
    Write-Host "  ~10 GB disk total (deps + model weights + native extensions)." -ForegroundColor Gray
    Write-Host ""
    $installTrellis = Ask-YesNo "  Install TRELLIS 2.0 now?" $true
} else {
    Write-Host ""
    Write-Host "==> [3/4] TRELLIS 2.0 install: SKIPPED (per --SkipTrellis)" -ForegroundColor DarkGray
}

if ($installTrellis) {
    # Pre-flight checks
    $needs = @()
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        $needs += "NVIDIA driver"
    }
    $cudaDir = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if (-not (Test-Path $cudaDir)) {
        $needs += "CUDA Toolkit 12.4 (https://developer.nvidia.com/cuda-12-4-0-download-archive)"
    }
    $msvc = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC"
    $hasGoodMsvc = $false
    if (Test-Path $msvc) {
        $hasGoodMsvc = (Get-ChildItem $msvc -Directory | Where-Object { $_.Name -match '^14\.(39|40)\.' }).Count -gt 0
    }
    if (-not $hasGoodMsvc) {
        $needs += "VS 2022 Build Tools + MSVC v14.40 toolset (Individual Components in VS Installer)"
    }

    if ($needs.Count -gt 0) {
        Write-Host ""
        Write-Host "  Missing prerequisites:" -ForegroundColor Red
        foreach ($n in $needs) { Write-Host "    - $n" -ForegroundColor Red }
        Write-Host "  Install these, then re-run scripts/install.ps1." -ForegroundColor Yellow
        Write-Host "  (CARMELA's BespokeAI cloud backend works without any of these.)" -ForegroundColor DarkGray
    } else {
        Write-Host "  All prerequisites detected. Installing..." -ForegroundColor Green
        & "$PSScriptRoot\install_trellis2.ps1"
        & "$PSScriptRoot\build_trellis2_native.ps1"
    }
}

# ---------- 3. smoke test + launch hint ----------

Write-Step "[4/4] Smoke test"
.venv\Scripts\python.exe scripts\smoke_test.py

Write-Host ""
Write-Host "✓ CARMELA installed." -ForegroundColor Green
Write-Host "  Launch: " -NoNewline; Write-Host ".\scripts\launch.ps1" -ForegroundColor Cyan
Pop-Location
