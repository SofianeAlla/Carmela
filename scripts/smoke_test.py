"""Quick sanity check: imports + library round-trip + (offline) CARLA connection."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backends import AssetRequest, list_backends
from carla_io import get_connection_status
from core import AssetLibrary, LibraryEntry, Orchestrator
from post_process import AVPostProcessConfig

print("=== Backends ===")
for s in list_backends():
    flag = "OK " if s.ok else "OFF"
    print(f"  {flag}  {s.name:20s}  {s.message}")

print("\n=== CARLA connection ===")
info = get_connection_status()
for k, v in info.items():
    print(f"  {k}: {v}")

print("\n=== Library ===")
lib = AssetLibrary()
print(f"  stats: {lib.stats()}")

print("\n=== Orchestrator dry-init ===")
orch = Orchestrator(library=lib)
print(f"  cache_dir: {orch.cache_dir}  exists={orch.cache_dir.exists()}")

print("\nOK.")
