"""Package post-processed GLBs into a CARLA-import-friendly folder.

CARLA's official asset import path (PythonAPI/util/import_assets.py)
expects FBX files plus a JSON file describing the assets. We:

 1. Convert each GLB -> FBX (best-effort, requires Blender CLI on PATH, else
    leaves the GLB and writes a manifest explaining how to do it manually).
 2. Emit the `<package>.json` index in CARLA's expected schema.
 3. Drop everything in <CARLA_ROOT>/Import/<package_name>/.

After that, the user runs (once, from a CARLA env with the editor):
   make import   # inside CARLA repo
or
   ./ImportAssets.sh / .bat from the package
to bake them into the Unreal project. We can't do that step from Python alone
unless the user has the editor; we surface the command in the manifest.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


def _find_blender() -> Optional[str]:
    for cand in ("blender", "blender.exe"):
        path = shutil.which(cand)
        if path:
            return path
    common = [
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
    ]
    for p in common:
        if os.path.exists(p):
            return p
    return None


def glb_to_fbx(glb: Path, fbx: Path) -> bool:
    """Convert via Blender CLI. Returns True on success."""
    blender = _find_blender()
    if not blender:
        log.warning("blender not found; cannot convert %s to FBX", glb)
        return False
    script = (
        "import bpy, sys; "
        "bpy.ops.wm.read_factory_settings(use_empty=True); "
        f"bpy.ops.import_scene.gltf(filepath=r'{glb}'); "
        f"bpy.ops.export_scene.fbx(filepath=r'{fbx}', use_selection=False, "
        "apply_unit_scale=True, bake_space_transform=True, axis_forward='X', axis_up='Z')"
    )
    cmd = [blender, "--background", "--python-expr", script]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        log.error("blender conversion of %s timed out", glb)
        return False
    if r.returncode != 0:
        log.error("blender failed for %s: %s", glb, r.stderr.decode("utf-8", "ignore")[-500:])
        return False
    return fbx.exists()


@dataclass
class PackageInput:
    asset_id: str
    glb_path: str
    asset_class: str
    metadata: dict


def prepare_carla_import_package(
    inputs: Iterable[PackageInput],
    package_name: str,
    carla_root: Optional[str] = None,
) -> dict:
    carla_root = carla_root or os.getenv("CARLA_ROOT", r"C:\Users\allas\CARLA_0.9.16")
    import_dir = Path(carla_root) / "Import" / package_name
    import_dir.mkdir(parents=True, exist_ok=True)

    package_props: list[dict] = []
    package_vehicles: list[dict] = []
    converted = 0
    skipped: list[str] = []

    for inp in inputs:
        glb = Path(inp.glb_path)
        if not glb.exists():
            skipped.append(f"missing: {glb}")
            continue
        fbx = import_dir / f"{inp.asset_id}.fbx"
        ok = glb_to_fbx(glb, fbx)
        if not ok:
            # Keep the GLB next to the slot so the user can convert by hand.
            shutil.copy(glb, import_dir / glb.name)
            skipped.append(inp.asset_id)
            continue
        converted += 1
        entry = {
            "name": inp.asset_id,
            "source": fbx.name,
            "size": "medium",
            "tag": inp.asset_class,
        }
        if inp.asset_class == "vehicle":
            entry["class"] = "car"
            entry["base_type"] = "car"
            entry["special_type"] = "standard"
            package_vehicles.append(entry)
        else:
            package_props.append(entry)

    manifest = {
        "props": package_props,
        "vehicles": package_vehicles,
        "skipped": skipped,
        "instructions": [
            "1. Open the CARLA Unreal Editor (or your CARLA source checkout).",
            f"2. Run: ImportAssets.bat (Windows) / ImportAssets.sh from {import_dir}",
            "3. Or via 'make import' in the CARLA build root if you have one.",
            "4. New blueprint ids will be e.g. static.prop.<asset_id> / vehicle.<asset_id>.",
        ],
    }
    manifest_path = import_dir / f"{package_name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "import_dir": str(import_dir),
        "manifest": str(manifest_path),
        "converted": converted,
        "skipped": skipped,
    }
