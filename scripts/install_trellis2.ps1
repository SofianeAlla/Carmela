# Install TRELLIS.2 locally on Windows with low-VRAM mode (8GB target).
# This is a separate, heavyweight install (~15 GB total: torch CUDA, model weights).
# Repo is cloned at ../external/TRELLIS.2. We create a dedicated venv next to it
# so its torch CUDA 12.4 install doesn't conflict with the FastAPI sidecar venv.

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$external = Resolve-Path (Join-Path $root "..\external")
$trellis = Join-Path $external "TRELLIS.2"
$venv = Join-Path $external ".venv-trellis2"

if (-not (Test-Path $trellis)) {
    Write-Host "[1/5] Cloning microsoft/TRELLIS.2..." -ForegroundColor Cyan
    Push-Location $external
    git clone --depth 1 https://github.com/microsoft/TRELLIS.2.git
    Pop-Location
} else {
    Write-Host "[1/5] TRELLIS.2 already cloned." -ForegroundColor DarkGray
}

if (-not (Test-Path $venv)) {
    Write-Host "[2/5] Creating dedicated venv (Python 3.10)..." -ForegroundColor Cyan
    uv venv --python 3.10 $venv
} else {
    Write-Host "[2/5] Venv already exists." -ForegroundColor DarkGray
}
$pyExe = Join-Path $venv "Scripts\python.exe"

Write-Host "[3/5] Installing torch 2.6.0 + CUDA 12.4..." -ForegroundColor Cyan
uv pip install --python $pyExe --index-strategy unsafe-best-match `
    --extra-index-url https://download.pytorch.org/whl/cu124 `
    "torch==2.6.0" "torchvision==0.21.0" "torchaudio==2.6.0"

Write-Host "[4/5] Installing TRELLIS.2 base deps..." -ForegroundColor Cyan
# These map to setup.sh --basic
uv pip install --python $pyExe `
    pillow imageio imageio-ffmpeg `
    "opencv-python>=4.10" `
    transformers diffusers accelerate `
    safetensors einops `
    huggingface_hub `
    trimesh `
    xatlas `
    pymeshlab `
    rembg `
    onnxruntime `
    numpy `
    scipy `
    "tqdm" `
    "easydict" `
    "kornia"

Write-Host "[4.5/5] Fetching model weights..." -ForegroundColor Cyan
# Zero-friction path: when CARMELA_MODELS_BUNDLE_URL is set in .env (or env),
# pull TRELLIS.2 / DINOv3 / RMBG-2.0 weights from the Carmela CDN — no HF
# auth, no license click-through. If it's NOT set, the user falls back to
# the manual HF flow (huggingface-cli login + license acceptance per model).
& "$PSScriptRoot\fetch_models_bundle.ps1"

Write-Host "[5/5] Pre-flight check..." -ForegroundColor Cyan
& $pyExe -c @"
import sys, torch
print(f'  python: {sys.version.split()[0]}')
print(f'  torch:  {torch.__version__}')
print(f'  cuda:   {torch.cuda.is_available()}  device count: {torch.cuda.device_count()}')
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print(f'  gpu:    {p.name}  vram: {p.total_memory/1e9:.1f}GB  cc {p.major}.{p.minor}')
try:
    sys.path.insert(0, r'$trellis')
    import trellis2  # noqa
    print('  trellis2 module importable.')
except Exception as e:
    print(f'  trellis2 import failed: {type(e).__name__}: {e}')
    print('  (Expected if o_voxel / cumesh native extensions are not built yet.)')
"@

Write-Host "`nNext steps:" -ForegroundColor Green
Write-Host "  - Optional (heavy):  o_voxel native build — needs Visual Studio Build Tools + CUDA 12.4 toolkit."
Write-Host "    cd $trellis\o-voxel; pip install -e ."
Write-Host "  - First-run model download (~8 GB) happens automatically when local_trellis2 is used."
Write-Host "  - 8GB VRAM laptops: expect CUDA OOM on 'hero' quality. Drop to 'draft' (512^3) and use offload."
