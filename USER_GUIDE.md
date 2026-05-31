# Carmela — User Guide

## Contents

1. [Backends](#backends)
2. [The desktop window](#the-desktop-window)
3. [Generate tab](#generate-tab)
4. [Batch tab](#batch-tab)
5. [Library tab](#library-tab)
6. [CARLA → Export Pipeline](#carla--export-pipeline)
7. [CARLA → Live Simulator](#carla--live-simulator)
8. [Settings](#settings)
9. [Post‑process pipeline](#post-process-pipeline)
10. [CARLA compliance](#carla-compliance)
11. [Troubleshooting](#troubleshooting)

---

## Backends

Carmela ships with two generation backends. The orchestrator picks the
healthiest one when you leave the **Backend** dropdown on `Auto`, or honors
your explicit choice.

| Backend | Where it runs | Time per asset | When to use |
|---|---|---|---|
| **`bespoke_api`** | Bespoke AI cloud (Supabase edge function) | ~30 s | First‑try default. No GPU needed locally. |
| **`local_trellis2`** | Your GPU, Microsoft TRELLIS.2 4B params | ~9 min on 8 GB at Draft (512³) | Offline use, full control over the pipeline, no API cost. |

Both backends expose the same `AssetRequest → AssetResult` contract, so
your library is backend‑agnostic — the metadata sidecar records which one
made each asset.

ComfyUI is intentionally *not* exposed in the UI even though the wrapper
lives in `backends/comfyui.py`; to re‑enable it set `COMFYUI_ENABLE=1` in
`.env`.

---

## The desktop window

```
┌──────────────┬──────────────────────────────────────────┐
│   sidebar    │                                          │
│ ─ Dashboard  │           tab content                    │
│ ─ Generate   │                                          │
│ ─ Batch      │                                          │
│ ─ Library    │                                          │
│              │                                          │
│ — CARLA —    │                                          │
│ ─ Export…    │                                          │
│ ─ Sim…       │                                          │
│              │                                          │
│ — System —   │                                          │
│ ─ Settings   │                                          │
│              │                                          │
│ status dots  │                                          │
└──────────────┴──────────────────────────────────────────┘
```

The status dots at the bottom of the sidebar reflect live backend health:

- **BespokeAI** — green when a valid `bspk_…` API key is present and the endpoint is reachable
- **TRELLIS.2** — green when the venv is built, native extensions imported, and weights cached
- **CARLA** — green when `carla.Client(host, port).get_server_version()` succeeds

The **topbar** shows the current section · page breadcrumb and a refresh
button to re‑poll all health checks.

---

## Generate tab

The bread‑and‑butter view.

### Inputs

| Field | Notes |
|---|---|
| **Text prompt** | Optional. Passed to `BespokeAI3DGeneration` if you have the BespokeAI ComfyUI node loaded, ignored by raw TRELLIS.2. |
| **Reference image** | Drop a PNG/JPG/WebP. RGBA images with a transparent background skip the rembg step entirely. |
| **Asset class** | Drives default real‑world height, default polycount, default CARLA semantic tag, and pivot heuristics. |
| **Quality** | `Draft` (512³, ~30 s Bespoke / 9 min local) · `Standard` (1024³, default for production) · `Hero` (1536³, OOMs on 8 GB). |
| **Seed** | Bit‑exact reproducibility. Same image + same seed = same output. |
| **Real height (m)** | Override the per‑class default. A 1.20 m traffic cone uses 1.20; an SUV uses 1.75. |
| **Output formats** | `GLB` (always) · `PLY` (geometry, no PBR) · `Collision hull` · `LOD chain` |
| **Backend** | `Auto`, `bespoke_api`, or `local_trellis2`. |

### Progress

The bar live‑tracks the backend:

- `5% Submitted · processing…`
- `5..95% Generating · N%` (linear in Bespoke's reported progress)
- `95% Downloading GLB…`
- `100% Done`

If something fails mid‑run the bar hides and a red toast surfaces the error.

### Live preview

three.js + `RoomEnvironment` PMREM for PBR, ACES tone mapping. Controls:

| Action | Mouse |
|---|---|
| **Orbit** | left drag |
| **Pan** | right drag (or middle drag) |
| **Zoom** | scroll wheel (zooms toward cursor) |
| **Recenter** | click `Recenter` button (top‑right of the canvas) |

The model is auto‑pivoted on its centroid (X/Z) with its lowest Y on the
grid plane. The grid auto‑scales to ~5× the model size so the asset never
feels lost or oversized in the frame.

### Downloads

After a successful generation:

- **Download GLB** — native save dialog
- **Download PLY** — only shown if PLY was requested
- **Open in folder** — reveals the library file in Explorer

### CARLA compliance panel

Six tiles on the right populate from sensible defaults you can override:

| Tile | What it controls |
|---|---|
| **Bounding box (tight)** | X × Y × Z extent in meters. Click **Edit…** for an interactive bbox configurator. |
| **Pivot point** | `ground-center` (default) · `ground-rear-axle` (vehicles) · `ground-front` · `bbox-center` · custom |
| **Semantic tag (CARLA)** | One of Vehicle (10), Pedestrian (4), TrafficSign (12), TrafficLight (7), Building (1), Fence (2), Vegetation (9), Pole (5), Dynamic (21), Other (22), Static (8) |
| **Collision shape** | `convex` (default — fast, accurate for most assets) · `box` · `mesh` (slow but precise) · `none` (decorative only) |
| **LOD targets** | Three tri‑count fields for LOD0 / LOD1 / LOD2 |
| **Vehicle physics** | Only when class=vehicle. Mass (kg), wheel radius (m). |

---

## Batch tab

Paste one prompt per line, set default class + quality + backend, click
**Run batch**. Jobs run sequentially in the sidecar's `ThreadPoolExecutor`
and each completed asset lands in the library.

The results table shows per‑job: prompt · class · backend · seconds · ok/err.

---

## Library tab

Filter chips at the top let you scope to one class. Each card shows the
prompt, class chip, asset id, and backend.

### Selection model

- **Click** a card → load preview + info panel
- **Shift‑click** or check the corner box → multi‑select for staging
- **Select all** button
- **Delete** button (in the detail panel) removes the entry from the index — files stay on disk

### Detail panel

- Sticky three.js preview (locked at 340 px height)
- Info card with thumbnail, prompt, class, quality, backend, duration, created date, credits used (Bespoke)
- File paths (GLB / metadata / PLY) with click‑to‑copy
- Action row: **Download GLB · Download PLY · Open in folder · Delete**

### Staging for CARLA

Select N assets, click **Stage X for CARLA** in the toolbar. Jumps to the
Export Pipeline tab with those IDs pre‑loaded.

---

## CARLA → Export Pipeline

Five numbered steps form a coherent workflow that matches CARLA's expected
asset ingestion path.

### 1 · CARLA target

- **CARLA root path** — e.g. `C:/Users/allas/CARLA_0.9.16`
- **Transport** — `Local install`, `Remote · SSH` (manual), `Remote · SMB / network share`
- **Remote host** (optional) — `user@build-farm.local:/opt/carla/Import` or a UNC path

Local mode writes directly. Remote mode stages locally and emits the right
`rsync` / `robocopy` one‑liner to move it.

### 2 · Package

- **Package name** — folder name under `<CARLA_ROOT>/Import/`
- **Naming convention** — `CARLA` style (`vehicle.brand.model`, `static.prop.name`) or `raw` (asset_id only)
- **Include collision meshes** — bundles the convex hulls as additional FBX entries
- **Include LODs** — bundles LOD1/LOD2 if generated

### 3 · Assets selected for export

The rows you staged from Library appear here. You can remove individual
entries before staging.

### 4 · Stage

Writes the package to disk:

- Converts each GLB → FBX via the Blender CLI (auto‑detected on PATH or `C:\Program Files\Blender Foundation\*`). If Blender isn't installed, the GLB is copied alongside and the manifest notes it.
- Emits the `<package>.json` index in CARLA's expected schema (props, vehicles, sizes, tags).

### 5 · Bake (Unreal‑side)

Carmela can't bake into the CARLA project directly — that needs Unreal
Editor or `make import` in a CARLA source tree. The card shows the exact
one‑liner with **Copy** for the package you just staged.

---

## CARLA → Live Simulator

Secondary tab for runtime sanity checks. Not the main workflow — most
asset work happens through Export.

- **Host / Port** — defaults to localhost:2000, configurable for remote sims
- **Check connection** — calls `Client.get_server_version()`
- **Launch local sim** — shells out to `<CARLA_ROOT>/CarlaUE4.exe`
- **Blueprint browser** — filter prefix → list of blueprint ids
- **Spawn** — pick a blueprint id, set X/Y/Z/Yaw, optionally use the random vehicle spawn point

Useful for verifying a blueprint id post‑import, or stress‑testing a map
with a random fleet.

---

## Settings

### BespokeAI API key

Get a key at https://bespokeai.build, paste it in the masked input, click
**Save & test**. The key is written to `.env` (`BESPOKE_API_KEY=bspk_…`)
and pushed into the sidecar's `os.environ` so it takes effect immediately
without restart. The pill shows live status.

### Backend health

Re‑poll button next to a table of all configured backends with their
status messages.

### TRELLIS 2.0 — local engine

Status‑only card showing the 5 native extensions as dots. If anything's
missing, re‑run `scripts/install.ps1`. The card never modifies system
state itself.

### Paths & env

Read‑only table of the variables Carmela reads from `.env`. Edit `.env` in
the project root and restart to change.

---

## Post‑process pipeline

Every successful generation goes through `post_process/mesh.py`:

1. **Load** the raw GLB into trimesh as a Scene
2. **Y‑up → Z‑up** rotation (Trellis/Bespoke output is Y‑up; Unreal/CARLA is Z‑up)
3. **Rescale** to the target real‑world height for that class
4. **Recenter** so XY = 0 and the lowest Z = 0 (model rests on the ground)
5. **Decimate** geometries above their class polycount target via fast‑quadric‑mesh‑simplification (when present)
6. **Convex collision hull** as a sidecar `*_collision.glb`
7. **Metadata JSON** with the applied scale, bounds, extent, tri count, class, and any custom fields

The output files land at `assets/library/<class>/<asset_id>_carla.glb`.
The metadata sidecar at `<asset_id>_carla.json` is what the Library tab's
info card and the Export Pipeline read.

---

## CARLA compliance

Why each compliance option matters:

### Collision hull

Physics engines (Bullet, PhysX, Chaos in Unreal) need *simple* geometry
to do contact detection. A 50 000‑tri visual mesh becomes a ~200‑face
convex hull for collisions. CARMELA defaults to `convex` because it's
the right trade‑off for vehicles, props, and signs. Use `box` for the
fastest path (barriers); `mesh` only when concavities matter (cargo
containers with open doors); `none` for purely decorative assets that
shouldn't collide.

### LOD chain

CARLA renders hundreds of vehicles and thousands of props per scene.
Without LODs you'd drop to single‑digit FPS. With three levels:

- **LOD0** — full detail (50 k tris) for assets within 15 m
- **LOD1** — medium (12 k tris) for 15–50 m
- **LOD2** — coarse (3 k tris) for 50 m+

CARMELA's post‑process can emit all three; Unreal's importer wires them
into a single StaticMesh with distance switching.

### Pivot point

CARLA expects assets to spawn at a specific pivot:

- **Vehicles** — rear‑axle ground center (so `spawn_actor` puts the rear
  axle on the road surface)
- **Pedestrians** — feet center
- **Props/signs** — base center on the ground

Picking the wrong pivot makes assets float, sink, or spin around their
center of mass.

### Semantic tag

CARLA's segmentation camera labels pixels by class id. Your generated
asset needs to be tagged so it shows up correctly in segmentation
output. The dropdown maps each label to its CARLA integer id (e.g.
Vehicle=10, TrafficSign=12).

### Bounding box

Two boxes, both editable in the modal:

- **Tight bbox (visual)** — used by the visualization camera for occlusion,
  raytracing acceleration, and editor selection
- **Collision bbox (physics)** — used for fast broad‑phase contact detection
  before the per‑triangle collision pass

Both default to the auto‑computed values from the mesh; the configurator
shows a 3D box overlay so you can adjust by eye + by number.

---

## Troubleshooting

### "BESPOKE_API_KEY not set" / Bespoke pill stays red

- Settings → BespokeAI API key → paste a `bspk_…` key → Save & test.
- The key must start with `bspk_`. The endpoint test should return `connected · bspk_xxx…xxxx`.

### TRELLIS.2 pill stays red

- Look at the message. The most common cases:
  - `external/TRELLIS.2 not cloned` → run `scripts/install.ps1` and answer **Y** to the TRELLIS prompt
  - `native extensions missing: cumesh, flex_gemm, …` → CUDA Toolkit 12.4 + MSVC v14.40 prerequisites missing; install both then re‑run `scripts/install_trellis2.ps1`
  - `DINOv3 ViT-L/16 (gated) missing` → accept the license at https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m then run `huggingface-cli login && huggingface-cli download facebook/dinov3-vitl16-pretrain-lvd1689m`

### "I generated a GLB but Rhino opens an empty window"

The earlier TRELLIS export used `EXT_texture_webp` which Rhino doesn't support. Files generated post‑fix use PNG textures and load everywhere. If you have older WebP‑textured GLBs, re‑export them via the inline trimesh conversion (see commit history) or simply re‑generate.

### "Preview doesn't appear after generation"

- Check the developer console (Ctrl+Shift+I) for a load error in the GLTFLoader.
- If the model loads but is invisible, it might be unlit (no env map). Carmela's viewer now ships a `RoomEnvironment` PMREM probe so this shouldn't happen — but if it does, click **Recenter** to reframe.
- If the canvas is empty, the host element might be 0×0 (rare timing bug). Switch tabs and back.

### Local TRELLIS.2 generation hits CUDA OOM

8 GB GPUs can't do Hero (1536³). Use Draft (512³) or Standard (1024³). The
runner already forces fp16 + sequential CPU offload via the `--low_vram`
flag, but the 1536³ activations still blow past 8 GB.

### CARLA spawn says "blueprint not found"

The blueprint you tried to spawn isn't in CARLA's blueprint library. Either:
- You haven't run the `ImportAssets` step in Unreal Editor yet — staging
  doesn't bake assets, it only prepares them
- You're using the wrong host/port (check Settings → Paths)
- The simulator isn't started — launch CarlaUE4.exe first
