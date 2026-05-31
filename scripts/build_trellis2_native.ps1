# Build TRELLIS.2 native extensions (FlexGEMM, CuMesh, o_voxel).
# Needs CUDA Toolkit 12.4 (nvcc) + Visual Studio 2022 Build Tools (cl, link).
# Idempotent — re-running skips already-installed extensions.

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$external = Resolve-Path (Join-Path $root "..\external")
$trellis = Join-Path $external "TRELLIS.2"
$venv = Join-Path $external ".venv-trellis2"
$py = Join-Path $venv "Scripts\python.exe"
$uv = "C:\Users\allas\.local\bin\uv.exe"
if (-not (Test-Path $uv)) {
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) { $uv = $uvCmd.Source } else { Write-Host "uv not found" -ForegroundColor Red; exit 1 }
}
if (-not (Test-Path $py)) {
    Write-Host "venv not found at $venv - run scripts/install_trellis2.ps1 first" -ForegroundColor Red
    exit 1
}

# Build deps required by --no-build-isolation
Write-Host "[build-deps] setuptools wheel ninja pybind11 ..." -ForegroundColor Cyan
& $uv pip install --python $py setuptools wheel ninja pybind11 | Out-Null

# --- nvcc detection ---
Write-Host "[pre-flight]" -ForegroundColor Cyan
$nvccCmd = Get-Command nvcc -ErrorAction SilentlyContinue
if (-not $nvccCmd) {
    $cudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if (Test-Path $cudaRoot) {
        $latest = Get-ChildItem $cudaRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
        if ($latest) {
            $env:CUDA_PATH = $latest.FullName
            $binPath = Join-Path $latest.FullName "bin"
            $libPath = Join-Path $latest.FullName "libnvvp"
            $env:PATH = "$binPath;$libPath;$env:PATH"
            $nvccCmd = Get-Command nvcc -ErrorAction SilentlyContinue
        }
    }
}
if (-not $nvccCmd) { Write-Host "nvcc not found." -ForegroundColor Red; exit 1 }
Write-Host "[nvcc] $($nvccCmd.Source)"

# --- VS Build Tools env ---
# vswhere lives in the VS Installer dir, not on PATH by default.
$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $env:PATH = "$(Split-Path $vswhere -Parent);$env:PATH"
} else {
    Write-Host "[warn] vswhere not found - VsDevCmd may misbehave" -ForegroundColor Yellow
}
$vsDevCmd = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
$msvcRoot = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC"
# CUDA 12.4 only supports MSVC <= 14.40. If both 14.40 and 14.44 are installed,
# select 14.40 explicitly via -vcvars_ver.
$preferredMsvc = ""
if (Test-Path $msvcRoot) {
    $candidates = Get-ChildItem $msvcRoot -Directory | Sort-Object Name
    $compatible = $candidates | Where-Object { $_.Name -match '^14\.(39|40)\.' } | Select-Object -First 1
    if ($compatible) {
        $preferredMsvc = "14.40"
        Write-Host "[msvc] using CUDA-12.4-compatible toolset $($compatible.Name)" -ForegroundColor Green
    } else {
        $newest = $candidates | Select-Object -Last 1
        Write-Host "[warn] only MSVC $($newest.Name) installed - CUDA 12.4 wants <=14.40, builds may fail" -ForegroundColor Yellow
    }
}
if (Test-Path $vsDevCmd) {
    Write-Host "[vsdevcmd] importing C++ build environment..." -ForegroundColor Cyan
    $vcArg = if ($preferredMsvc) { "-vcvars_ver=$preferredMsvc" } else { "" }
    $envOut = & cmd /c "`"$vsDevCmd`" -arch=x64 -host_arch=x64 $vcArg 1>nul 2>nul && set"
    if ($envOut) {
        foreach ($line in $envOut) {
            if ($line -match '^([^=]+)=(.*)$') {
                [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
            }
        }
    }
}
# Sanity check the toolchain
$cl = Get-Command cl -ErrorAction SilentlyContinue
if ($cl) { Write-Host "[cl] $($cl.Source)" } else { Write-Host "[warn] cl.exe not on PATH" -ForegroundColor Yellow }

# Required by PyTorch C++ extension builder when VC env is already active.
$env:DISTUTILS_USE_SDK = "1"
# Help nvcc find a sensible host compiler when the user has both VS Build Tools and VS proper.
if (-not $env:TORCH_CUDA_ARCH_LIST) {
    # 8.9 = RTX 4070 Laptop (Ada). Building only for this arch saves ~half the time.
    $env:TORCH_CUDA_ARCH_LIST = "8.9"
}

$extDir = Join-Path $external "ext-build"
New-Item -ItemType Directory -Path $extDir -Force | Out-Null

function Install-Repo {
    param([string]$Name, [string]$Url, [string]$Branch = "")
    $dst = Join-Path $script:extDir $Name
    if (-not (Test-Path $dst)) {
        Write-Host "[clone] $Name $(if ($Branch) { "($Branch)" })" -ForegroundColor Cyan
        $args = @("clone", "--recursive")
        if ($Branch) { $args += @("-b", $Branch) }
        $args += @($Url, $dst)
        & git @args 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] git clone $Name" -ForegroundColor Red; return $false }
    }
    Write-Host "[build] $Name - this can take 5-15 min..." -ForegroundColor Cyan
    $logFile = Join-Path $script:external "$Name.log"
    # Redirect uv output to log via cmd to avoid PS stderr-as-error behaviour.
    $cmdLine = "`"$script:uv`" pip install --python `"$script:py`" `"$dst`" --no-build-isolation -v"
    & cmd /c "$cmdLine > `"$logFile`" 2>&1"
    $rc = $LASTEXITCODE
    # Sanity: confirm the module actually shows up
    $modName = $Name.ToLower() -replace 'gemm','_gemm'
    & $script:py -c "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('$modName') else 7)" 2>&1 | Out-Null
    $importOk = ($LASTEXITCODE -eq 0)
    if ($rc -ne 0 -or -not $importOk) {
        Write-Host "[FAIL] $Name - tail of ${logFile} :" -ForegroundColor Red
        Get-Content $logFile -Tail 30 -ErrorAction SilentlyContinue
        return $false
    }
    Write-Host "[OK]   $Name" -ForegroundColor Green
    return $true
}

$results = @{}
$results.FlexGEMM   = Install-Repo -Name "FlexGEMM"   -Url "https://github.com/JeffreyXiang/FlexGEMM.git"
$results.CuMesh     = Install-Repo -Name "CuMesh"     -Url "https://github.com/JeffreyXiang/CuMesh.git"
# Renderers — optional for inference but required for the texturing pipeline.
$results.nvdiffrast = Install-Repo -Name "nvdiffrast" -Url "https://github.com/NVlabs/nvdiffrast.git"
$results.nvdiffrec  = Install-Repo -Name "nvdiffrec"  -Url "https://github.com/JeffreyXiang/nvdiffrec.git" -Branch "renderutils"

Write-Host "[build] o_voxel (vendored)" -ForegroundColor Cyan
$ovx = Join-Path $trellis "o-voxel"
$ovxLog = Join-Path $external "o_voxel.log"
# --no-deps skips re-fetching cumesh + flex_gemm from git (they're already installed locally).
$ovxCmd = "`"$uv`" pip install --python `"$py`" `"$ovx`" --no-build-isolation --no-deps -v"
& cmd /c "$ovxCmd > `"$ovxLog`" 2>&1"
$ovxRc = $LASTEXITCODE
& $py -c "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('o_voxel') else 7)" 2>&1 | Out-Null
$ovxImportOk = ($LASTEXITCODE -eq 0)
if ($ovxRc -ne 0 -or -not $ovxImportOk) {
    Write-Host "[FAIL] o_voxel - tail of ${ovxLog} :" -ForegroundColor Red
    Get-Content $ovxLog -Tail 30 -ErrorAction SilentlyContinue
    $results.o_voxel = $false
} else {
    Write-Host "[OK]   o_voxel" -ForegroundColor Green
    $results.o_voxel = $true
}

Write-Host ""
Write-Host "[verify]" -ForegroundColor Cyan
$mods = @('cumesh','flex_gemm','o_voxel','nvdiffrast','nvdiffrec_render')
$verifyParts = $mods | ForEach-Object { "print('$_'.ljust(12), 'OK' if importlib.util.find_spec('$_') else 'MISSING')" }
$verifyBody = $verifyParts -join '; '
$verifyCmd = "`"$py`" -c `"import importlib.util, sys; sys.path.insert(0, r'$trellis'); $verifyBody`""
cmd /c $verifyCmd

Write-Host ""
Write-Host "[summary]" -ForegroundColor Cyan
foreach ($k in $results.Keys) {
    $msg = if ($results[$k]) { "OK" } else { "FAIL - see $external\$k.log" }
    $col = if ($results[$k]) { "Green" } else { "Red" }
    Write-Host ("  {0,-12} {1}" -f $k, $msg) -ForegroundColor $col
}
