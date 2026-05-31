"""On-disk asset library.

Layout under <root>/:
   <asset_class>/<asset_id>_carla.glb
   <asset_class>/<asset_id>_collision.glb
   <asset_class>/<asset_id>_carla.json
   index.json  (master index)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from backends.base import AssetRequest


class LibraryEntry(BaseModel):
    asset_id: str
    request: AssetRequest
    backend: str
    raw_glb: str
    carla_glb: str
    collision_glb: Optional[str] = None
    metadata_path: str
    duration_s: float
    created_at: str = ""

    def with_timestamp(self) -> "LibraryEntry":
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        return self


class AssetLibrary:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or os.getenv("ASSET_LIBRARY_DIR", "./assets/library"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text("[]", encoding="utf-8")

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _write(self, items: list[dict]) -> None:
        self.index_path.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")

    def add(self, entry: LibraryEntry) -> LibraryEntry:
        entry = entry.with_timestamp()
        items = self._read()
        # de-dupe by asset_id
        items = [i for i in items if i.get("asset_id") != entry.asset_id]
        items.append(json.loads(entry.model_dump_json()))
        self._write(items)
        return entry

    def remove(self, asset_id: str) -> bool:
        items = self._read()
        new = [i for i in items if i.get("asset_id") != asset_id]
        if len(new) == len(items):
            return False
        self._write(new)
        return True

    def list(self, asset_class: Optional[str] = None) -> list[LibraryEntry]:
        items = self._read()
        if asset_class:
            items = [i for i in items if i.get("request", {}).get("asset_class") == asset_class]
        out: list[LibraryEntry] = []
        for i in items:
            try:
                out.append(LibraryEntry(**i))
            except Exception:  # noqa: BLE001, S110
                continue
        return out

    def find_by_cache_key(self, key: str) -> Optional[LibraryEntry]:
        for entry in self.list():
            if entry.asset_id == key and Path(entry.carla_glb).exists():
                return entry
        return None

    def stats(self) -> dict:
        items = self.list()
        by_class: dict[str, int] = {}
        for e in items:
            c = e.request.asset_class
            by_class[c] = by_class.get(c, 0) + 1
        return {
            "total": len(items),
            "by_class": by_class,
            "root": str(self.root),
        }
