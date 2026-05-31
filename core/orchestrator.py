"""Orchestrates generate -> post-process -> library write."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Iterable, Optional

from backends import (
    AssetRequest,
    AssetResult,
    BackendError,
    list_backends,
    select_backend,
)
from post_process import AVPostProcessConfig, av_post_process

from .library import AssetLibrary, LibraryEntry

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        library: Optional[AssetLibrary] = None,
        cache_dir: Optional[str] = None,
        preferred_backend: Optional[str] = None,
    ):
        self.library = library or AssetLibrary()
        self.cache_dir = Path(cache_dir or os.getenv("ASSET_CACHE_DIR", "./assets/cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.preferred_backend = preferred_backend

    # ----- generation -----

    def generate_one(
        self,
        req: AssetRequest,
        post_cfg: Optional[AVPostProcessConfig] = None,
        save_to_library: bool = True,
        backend_name: Optional[str] = None,
    ) -> LibraryEntry:
        cached = self.library.find_by_cache_key(req.cache_key())
        if cached is not None:
            log.info("cache hit for %s", req.cache_key())
            return cached

        backend = select_backend(backend_name or self.preferred_backend)
        t0 = time.time()
        try:
            result = backend.generate(req, self.cache_dir)
        except BackendError as e:
            log.warning("primary backend %s failed: %s", backend.name, e)
            # Fall back through the remaining healthy backends.
            for s in list_backends():
                if not s.ok or s.name == backend.name:
                    continue
                alt = select_backend(s.name)
                try:
                    result = alt.generate(req, self.cache_dir)
                    break
                except BackendError as alt_err:  # noqa: PERF203
                    log.warning("fallback %s also failed: %s", s.name, alt_err)
            else:
                raise
        result.save_sidecar()

        post_cfg = post_cfg or AVPostProcessConfig()
        artifacts = av_post_process(
            src_glb=result.glb_path,
            out_dir=self.library.root / req.asset_class,
            asset_class=req.asset_class,
            cfg=post_cfg,
        )

        entry = LibraryEntry(
            asset_id=req.cache_key(),
            request=req,
            backend=result.backend,
            raw_glb=result.glb_path,
            carla_glb=artifacts["glb"],
            collision_glb=artifacts.get("collision_glb"),
            metadata_path=artifacts["metadata"],
            duration_s=time.time() - t0,
        )
        if save_to_library:
            self.library.add(entry)
        return entry

    def generate_batch(
        self,
        requests: Iterable[AssetRequest],
        post_cfg: Optional[AVPostProcessConfig] = None,
        on_progress=None,
        backend_name: Optional[str] = None,
    ) -> list[LibraryEntry]:
        out: list[LibraryEntry] = []
        reqs = list(requests)
        for i, r in enumerate(reqs):
            try:
                entry = self.generate_one(r, post_cfg, backend_name=backend_name)
                out.append(entry)
                if on_progress:
                    on_progress(i + 1, len(reqs), entry)
            except Exception as e:  # noqa: BLE001
                log.exception("batch item %d failed: %s", i, e)
                if on_progress:
                    on_progress(i + 1, len(reqs), None)
        return out

    def generate_variations(
        self,
        base: AssetRequest,
        n: int,
        seed_start: int = 0,
        backend_name: Optional[str] = None,
    ) -> list[LibraryEntry]:
        reqs = [
            base.model_copy(update={"seed": seed_start + i})
            for i in range(n)
        ]
        return self.generate_batch(reqs, backend_name=backend_name)
