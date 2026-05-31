<div align="center">

<img src="desktop/renderer/assets/moose-logo.png" alt="Carmela" width="120"/>

# Carmela

**Free 3D AI Asset Generation for the CARLA Simulator**

*by [Bespoke AI](https://bespokeai.build)*

A desktop application that turns a single image into a CARLA‑ready 3D asset
in minutes — fully textured GLB, real‑world scaled, with collision hulls
and CARLA Import packaging baked in.

[Install](#install) · [Quick start](#quick-start) · [User guide](USER_GUIDE.md) · [Architecture](#architecture)

</div>

---

## What Carmela does

Autonomous‑driving research lives or dies by the **long tail of scenarios**.
CARLA ships with a great default catalogue of vehicles, pedestrians and
props — but the regional sign variants, debris, novel vehicle silhouettes,
weathered street furniture and edge‑case objects you need for real
domain‑coverage testing aren't there.

Carmela closes that gap. Drop an image of any object → 9 minutes later
you have:

- A PBR‑textured **GLB** suitable for Unreal / CARLA
- A **PLY** point/mesh export for downstream pipelines
- A **convex collision hull** ready for physics
- Real‑world **scale**, **axis orientation** (Y‑up → Z‑up), and **pivot point** matching CARLA conventions
- A **semantic tag** (Vehicle / Pedestrian / TrafficSign / …) for CARLA's segmentation camera
- A **staged Import package** at `<CARLA_ROOT>/Import/<pack>/` ready for the `ImportAssets` step

All running on a single laptop with an 8 GB GPU.

## What's inside

```
 ┌─────────────────────────────────┐
 │  Electron desktop (renderer)    │  vanilla HTML/CSS/JS + three.js
 │  · Dashboard / Generate / Batch │  PBR preview · progress bar · downloads
 │  · Library · CARLA · Settings   │  Bespoke‑themed (DM Sans + Instrument Serif)
 └────────────────┬────────────────┘
                  │  HTTP localhost
 ┌────────────────▼────────────────┐
 │  FastAPI Python sidecar         │  uvicorn · job queue · /generate /library
 │  · Orchestrator + post-process  │  /carla/* /system/* /backends/health
 └────────────────┬────────────────┘
                  │
   ┌──────────────┼────────────────┬──────────────────┐
   ▼              ▼                ▼                  ▼
 BespokeAI    Local TRELLIS.2   (no comfyui)    CARLA 0.9.16
 cloud API    8 GB low-VRAM                     PythonAPI client
              fp16 + offload                    + Import pipeline
```

| Layer | What's there |
|---|---|
| **Backends** | `bespoke_api` (cloud, fast, ~30 s per asset) · `local_trellis2` (8 GB VRAM in fp16 + sequential CPU offload, ~9 min) |
| **Post‑process** | trimesh: axis fix · rescale to class height · convex collision hull · decimate to target tri count · sidecar JSON |
| **CARLA I/O** | `carla.Client` wrapper · blueprint listing · runtime spawn · `ImportAssets` package builder (FBX via Blender CLI when present) |
| **Library** | per‑class on‑disk DB with thumbnail, metadata sidecar, batch staging |

## Install

### Prerequisites

| Requirement | Where |
|---|---|
| **Windows 10/11** with PowerShell | (Linux/macOS work but installer is Windows‑first) |
| **CARLA 0.9.16** | https://github.com/carla-simulator/carla/releases/tag/0.9.16 — extract to `C:\Users\<you>\CARLA_0.9.16\` |
| **Python 3.10** | https://www.python.org/downloads/ |
| **Node.js 20+** | https://nodejs.org/ |
| **uv** | `winget install astral-sh.uv` |
| **NVIDIA driver + GPU (8 GB+)** | for local TRELLIS.2 — RTX 3060/4060/4070 or better |
| **NVIDIA CUDA Toolkit 12.4** | for local TRELLIS.2 — https://developer.nvidia.com/cuda-12-4-0-download-archive (only the *Compiler* + *Libraries* + *VS Integration*) |
| **Visual Studio 2022 Build Tools + MSVC v14.40 toolset** | for local TRELLIS.2 — CUDA 12.4 doesn't support v14.44+. In VS Installer: **Individual components → MSVC v143 - VS 2022 C++ x64/x86 build tools (v14.40-17.10)** |

### One‑shot installer

```powershell
git clone https://github.com/SofianeAlla/Carmela.git
cd Carmela
.\scripts\install.ps1
```

The installer:
1. Builds the FastAPI sidecar venv (Python 3.10) + installs `carla 0.9.16` Python client
2. Runs `npm install` in `desktop/`
3. Copies `.env.example → .env`
4. **Prompts** to install TRELLIS 2.0 (highly recommended). If you say yes:
   - Clones `microsoft/TRELLIS.2` into `external/`
   - Builds a dedicated `.venv-trellis2` with torch 2.6 + CUDA 12.4
   - Compiles the 5 native CUDA extensions (cumesh, flex_gemm, o_voxel, nvdiffrast, nvdiffrec)
   - Downloads ~14 GB of model weights to the HuggingFace cache

To skip TRELLIS install, pass `-SkipTrellis`. To accept all prompts non‑interactively, pass `-Yes`.

### Run

```powershell
.\scripts\launch.ps1
```

Opens the Carmela desktop window. The Python sidecar is spawned automatically; killing the window kills it.

### Standalone build (.exe + installer)

```powershell
cd desktop
npm run build:win
```

Produces an NSIS installer in `desktop/dist/`.

### Zero‑friction install for end users (shipping the model bundle)

By default, the installer routes model weight downloads through HuggingFace —
which means end users would need an HF account, a token, and to accept the
DINOv3 + RMBG‑2.0 licenses. **Carmela supports a one‑variable override** so
you can ship a CDN bundle and your users get everything in one step:

1. **One time, on your dev machine** (where you've already accepted the
   licenses): pack your local HF cache into a CDN‑ready archive set —
   ```powershell
   .\scripts\pack_models_bundle.ps1 -Split
   ```
   Produces `dist-bundle/manifest.json` + one `*.tar.zst` per model repo
   (TRELLIS.2‑4B, TRELLIS‑image‑large, DINOv3 ViT‑L/16, RMBG‑2.0). About
   ~13 GB compressed.

2. **Upload** the contents of `dist-bundle/` to your CDN (S3, R2, Bespoke's
   Supabase bucket, GitHub Releases, anything that serves static files).

3. **Set the URL** in the installer's `.env` (or `.env.example` shipped with
   Carmela) so it's baked into the installed app:
   ```ini
   CARMELA_MODELS_BUNDLE_URL=https://cdn.bespokeai.build/carmela/v1
   ```

4. **End users** now run `.\scripts\install.ps1` and `fetch_models_bundle.ps1`
   pulls the archives straight from your CDN into their HF cache — no HF
   auth, no license click‑through, no `WinError 10054` retry loop.

The dev path (HF + tokens + licenses) is still available when
`CARMELA_MODELS_BUNDLE_URL` is empty.

## Quick start

1. Launch Carmela.
2. Go to **Settings → BespokeAI API key**, paste your `bspk_…` key, click **Save & test**. (Skip if you only want local TRELLIS.)
3. Go to **Generate**.
4. Drop a reference image into the dropzone.
5. Set **Class** = `prop` (or whatever fits), **Quality** = `Draft` (fast) or `Standard` (production).
6. Click **Generate asset**. The progress bar tracks Bespoke's polling; ~30 s for `bespoke_api`, ~9 min for `local_trellis2` Draft.
7. Inspect the PBR preview in the right pane. **Recenter** if needed.
8. Hit **Download GLB**, or jump to **Library** for the full asset list and CARLA staging.

## Architecture

See [`USER_GUIDE.md`](USER_GUIDE.md) for the detailed walkthrough of each tab,
the backend abstraction, the CARLA compliance options (bbox, LOD, pivot,
semantic tag), and the export pipeline.

## License

MIT. Model weights (TRELLIS.2, DINOv3, RMBG‑2.0) keep their original licenses
which Carmela's installer accepts on your behalf at install time.
