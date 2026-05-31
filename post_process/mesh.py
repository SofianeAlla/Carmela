"""Post-process a freshly generated GLB so CARLA / Unreal Engine can use it.

What we do:
 1. Center the mesh on the ground origin (XY at 0, lowest Z at 0).
 2. Rotate from Trellis' Y-up convention to Unreal's Z-up.
 3. Rescale to a real-world meter target per asset class.
 4. Optionally decimate to a target tri count (for CARLA perf at scale).
 5. Generate a convex collision hull as a sidecar GLB.
 6. Write a sidecar JSON with semantic class, bbox, scale factor.

Output of post_process_for_carla() is a dict pointing at all artifacts.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import trimesh

log = logging.getLogger(__name__)


# Default heuristics for real-world size in meters (height of the asset).
# Used when we can't infer scale from the source image. These are
# deliberately broad — fine for prototypes, tune per project.
DEFAULT_HEIGHT_M = {
    "vehicle": 1.50,
    "pedestrian": 1.75,
    "prop": 1.00,
    "sign": 2.20,
    "debris": 0.30,
    "vegetation": 3.00,
    "barrier": 1.10,
    "other": 1.00,
}

DEFAULT_POLYCOUNT = {
    "vehicle": 50_000,
    "pedestrian": 25_000,
    "prop": 8_000,
    "sign": 2_000,
    "debris": 4_000,
    "vegetation": 12_000,
    "barrier": 4_000,
    "other": 8_000,
}


@dataclass
class AVPostProcessConfig:
    target_height_m: Optional[float] = None
    target_polycount: Optional[int] = None
    yup_to_zup: bool = True
    center_on_ground: bool = True
    make_collision_hull: bool = True
    metadata: dict = field(default_factory=dict)


def estimate_real_world_scale(asset_class: str, hint_height_m: Optional[float] = None) -> float:
    if hint_height_m is not None:
        return float(hint_height_m)
    return DEFAULT_HEIGHT_M.get(asset_class, 1.0)


def _load_scene(path: Path) -> trimesh.Scene:
    obj = trimesh.load(path, force="scene")
    if isinstance(obj, trimesh.Trimesh):
        scene = trimesh.Scene()
        scene.add_geometry(obj)
        return scene
    return obj


def _combined_mesh(scene: trimesh.Scene) -> trimesh.Trimesh:
    geoms = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
    if not geoms:
        raise ValueError("scene has no triangle meshes")
    if len(geoms) == 1:
        return geoms[0]
    return trimesh.util.concatenate(geoms)


def _yup_to_zup_matrix() -> np.ndarray:
    # Rotate +90deg around X: Y -> Z, Z -> -Y.
    return trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])


def av_post_process(
    src_glb: str | Path,
    out_dir: str | Path,
    asset_class: str = "prop",
    cfg: Optional[AVPostProcessConfig] = None,
) -> dict:
    cfg = cfg or AVPostProcessConfig()
    src = Path(src_glb)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scene = _load_scene(src)
    combined = _combined_mesh(scene)

    transform = np.eye(4)
    if cfg.yup_to_zup:
        transform = _yup_to_zup_matrix() @ transform

    target_h = cfg.target_height_m or DEFAULT_HEIGHT_M.get(asset_class, 1.0)
    # Apply the rotation first, then measure, then scale.
    rotated = combined.copy()
    rotated.apply_transform(transform)
    bbox = rotated.bounding_box.extents  # (x, y, z) in current units
    current_h = float(bbox[2])
    if current_h <= 1e-6:
        log.warning("mesh has zero height; skipping rescale")
        scale = 1.0
    else:
        scale = target_h / current_h
    S = trimesh.transformations.scale_matrix(scale)
    transform = S @ transform

    if cfg.center_on_ground:
        # Re-measure post scale+rotate, then translate so XY=0 and z_min=0.
        rotated_scaled = combined.copy()
        rotated_scaled.apply_transform(transform)
        bbox = rotated_scaled.bounds  # 2x3
        dx = -(bbox[0, 0] + bbox[1, 0]) / 2
        dy = -(bbox[0, 1] + bbox[1, 1]) / 2
        dz = -bbox[0, 2]
        T = trimesh.transformations.translation_matrix([dx, dy, dz])
        transform = T @ transform

    # Apply final transform to every geom in the scene so textures survive.
    for geom_name, geom in list(scene.geometry.items()):
        if isinstance(geom, trimesh.Trimesh):
            geom.apply_transform(transform)

    # Decimate. trimesh's simplify uses fast-quadric-mesh-simplification if
    # available; otherwise we just skip and log.
    target_tri = cfg.target_polycount or DEFAULT_POLYCOUNT.get(asset_class, 8_000)
    for geom_name, geom in list(scene.geometry.items()):
        if not isinstance(geom, trimesh.Trimesh):
            continue
        tri_count = len(geom.faces)
        if tri_count > target_tri * 1.2:
            try:
                ratio = target_tri / tri_count
                simplified = geom.simplify_quadric_decimation(ratio)
                if isinstance(simplified, trimesh.Trimesh) and len(simplified.faces) > 0:
                    scene.geometry[geom_name] = simplified
            except Exception as e:  # noqa: BLE001
                log.warning("decimation failed for %s: %s", geom_name, e)

    out_glb = out_dir / f"{src.stem}_carla.glb"
    scene.export(out_glb)

    artifacts = {"glb": str(out_glb)}

    if cfg.make_collision_hull:
        final_mesh = _combined_mesh(scene)
        try:
            hull = final_mesh.convex_hull
            out_collision = out_dir / f"{src.stem}_collision.glb"
            hull.export(out_collision)
            artifacts["collision_glb"] = str(out_collision)
        except Exception as e:  # noqa: BLE001
            log.warning("collision hull failed: %s", e)

    final_mesh = _combined_mesh(scene)
    bounds = final_mesh.bounds.tolist()
    meta = {
        "source_glb": str(src),
        "asset_class": asset_class,
        "scale_factor_applied": float(scale),
        "rotation_y_to_z": cfg.yup_to_zup,
        "target_height_m": target_h,
        "bounds_m": bounds,
        "extent_m": [
            bounds[1][0] - bounds[0][0],
            bounds[1][1] - bounds[0][1],
            bounds[1][2] - bounds[0][2],
        ],
        "tri_count": int(len(final_mesh.faces)),
        "extra": cfg.metadata,
    }
    out_meta = out_dir / f"{src.stem}_carla.json"
    out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    artifacts["metadata"] = str(out_meta)
    return artifacts
