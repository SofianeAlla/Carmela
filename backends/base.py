"""Backend abstraction for 3D asset generation.

Every backend takes an `AssetRequest` (prompt + optional image + class label +
quality settings) and returns an `AssetResult` pointing at a GLB on disk plus
metadata. The orchestrator handles caching, retries, and fallback between
backends; backends themselves only have to produce a mesh.
"""
from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


AssetClass = Literal[
    "vehicle",
    "pedestrian",
    "prop",
    "sign",
    "debris",
    "vegetation",
    "barrier",
    "other",
]


class AssetRequest(BaseModel):
    prompt: str = Field(..., description="Text description of the asset")
    image_path: Optional[str] = Field(
        None, description="Optional reference image (file path). TRELLIS.2 is image-to-3D."
    )
    asset_class: AssetClass = "prop"
    seed: int = 0
    # Quality dial: 'draft' = fast preview, 'standard' = production, 'hero' = max fidelity.
    quality: Literal["draft", "standard", "hero"] = "standard"
    pbr: bool = True
    polycount: Optional[int] = Field(
        None,
        description="Target tri count after post-processing. None = keep native.",
    )

    def cache_key(self) -> str:
        """Stable hash, used to cache identical requests across backends."""
        blob = self.model_dump_json(exclude={"image_path"}).encode()
        if self.image_path and Path(self.image_path).exists():
            blob += Path(self.image_path).read_bytes()
        return hashlib.sha1(blob).hexdigest()[:16]


class AssetResult(BaseModel):
    request: AssetRequest
    glb_path: str
    backend: str
    duration_s: float
    raw_response: dict = Field(default_factory=dict)
    preview_image: Optional[str] = None

    def save_sidecar(self) -> None:
        meta = self.model_dump()
        Path(self.glb_path).with_suffix(".json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )


class BackendError(Exception):
    """Recoverable backend failure (network, rate limit) — orchestrator should retry/fallback."""


class BackendUnavailable(BackendError):
    """Backend is not configured (missing keys, no GPU, etc.). Skip silently."""


class Backend(abc.ABC):
    name: str = "abstract"
    supports_text_only: bool = False
    supports_image: bool = True
    requires_internet: bool = False

    def health_check(self) -> tuple[bool, str]:
        """Return (ok, message). Default: assume ok."""
        return True, "ok"

    # Optional progress callback. The orchestrator wires this so the
    # API/UI can show a live bar. Backends should call it with
    # progress_cb(percent: float 0..100, message: str | None).
    progress_cb = None

    @abc.abstractmethod
    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        """Generate a GLB. Must write to out_dir / "<cache_key>.glb"."""


@dataclass
class BackendStatus:
    name: str
    ok: bool
    message: str
    config: dict = field(default_factory=dict)
