"""Runtime spawning helpers.

CARLA spawns actors from blueprints registered in the simulator. New 3D meshes
must be imported into the UE4 project before they can be spawned with their
own blueprint id (see `carla_io/package.py`). However, you can already spawn
ANY existing blueprint (vehicles, walkers, static props) from this module —
the typical workflow is:

  1. Generate + post-process new assets (Trellis pipeline).
  2. Package them and run import (`prepare_carla_import_package`).
  3. Rebuild the CARLA package (Unreal Editor or `make package`).
  4. Spawn the new blueprint at runtime here.

For users without a UE4 editor (the common case), we offer a "spawn nearest
matching existing prop" helper that picks the closest CARLA built-in prop by
semantic class — useful for previewing layouts while the proper import is
queued.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

from .client import CarlaConnection

log = logging.getLogger(__name__)

SEMANTIC_TO_PREFIX = {
    "vehicle": "vehicle.",
    "pedestrian": "walker.pedestrian.",
    "prop": "static.prop.",
    "sign": "static.prop.",
    "debris": "static.prop.",
    "barrier": "static.prop.",
    "vegetation": "static.prop.",
    "other": "static.prop.",
}


def spawn_vehicle_by_blueprint(
    blueprint_id: str,
    conn: Optional[CarlaConnection] = None,
    autopilot: bool = True,
) -> dict:
    conn = conn or CarlaConnection()
    world = conn.world()
    bp = world.get_blueprint_library().find(blueprint_id)
    if not bp:
        raise ValueError(f"blueprint {blueprint_id} not found in CARLA library")
    spawns = world.get_map().get_spawn_points()
    if not spawns:
        raise RuntimeError("map has no spawn points")
    sp = random.choice(spawns)
    actor = world.spawn_actor(bp, sp)
    if autopilot and blueprint_id.startswith("vehicle."):
        actor.set_autopilot(True)
    return {
        "actor_id": actor.id,
        "type_id": actor.type_id,
        "location": [sp.location.x, sp.location.y, sp.location.z],
    }


def spawn_static_prop(
    blueprint_id: str,
    location: tuple[float, float, float],
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
    conn: Optional[CarlaConnection] = None,
) -> dict:
    import carla  # noqa: PLC0415

    conn = conn or CarlaConnection()
    world = conn.world()
    bp = world.get_blueprint_library().find(blueprint_id)
    if not bp:
        raise ValueError(f"blueprint {blueprint_id} not found")
    transform = carla.Transform(
        carla.Location(*location),
        carla.Rotation(pitch=rotation_deg[0], yaw=rotation_deg[1], roll=rotation_deg[2]),
    )
    actor = world.spawn_actor(bp, transform)
    return {"actor_id": actor.id, "type_id": actor.type_id}


def nearest_existing_prop(asset_class: str, conn: Optional[CarlaConnection] = None) -> Optional[str]:
    """Pick a built-in CARLA prop closest in semantic class. Preview crutch."""
    prefix = SEMANTIC_TO_PREFIX.get(asset_class, "static.prop.")
    conn = conn or CarlaConnection()
    world = conn.world()
    bps = world.get_blueprint_library().filter(prefix + "*")
    bp_ids = sorted([bp.id for bp in bps])
    return bp_ids[0] if bp_ids else None
