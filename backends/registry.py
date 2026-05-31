"""Backend registry: configure once, pick the best one available."""
from __future__ import annotations

import logging
import os
from typing import Optional

from .base import Backend, BackendStatus

log = logging.getLogger(__name__)

# Default preference order. Highest fidelity first; orchestrator falls back
# the first one to fail or report unavailable.
# `comfyui` is primary (runs locally, extensible workflow). `bespoke_api` is
# the no-server fallback that hits the same Bespoke service directly.
# `local_trellis2` only activates on a 24GB+ GPU.
# ComfyUI is intentionally NOT in the priority list — the user wants the
# Bespoke API + local TRELLIS.2 as the public-facing options. The comfyui
# backend module is kept so it can be re-enabled later by appending its name
# here (or via the COMFYUI_ENABLE env flag).
DEFAULT_PRIORITY = [
    "bespoke_api",
    "local_trellis2",
]
if os.getenv("COMFYUI_ENABLE", "0") == "1":
    DEFAULT_PRIORITY.insert(0, "comfyui")

_singletons: dict[str, Backend] = {}


def _build(name: str) -> Backend:
    if name == "comfyui":
        from .comfyui import ComfyUIBackend  # noqa: PLC0415

        return ComfyUIBackend()
    if name == "bespoke_api":
        from .bespoke_api import BespokeBackend  # noqa: PLC0415

        return BespokeBackend()
    if name == "local_trellis2":
        from .local_trellis2 import LocalTrellis2Backend  # noqa: PLC0415

        return LocalTrellis2Backend()
    raise KeyError(f"unknown backend: {name}")


def get_backend(name: str) -> Backend:
    if name not in _singletons:
        _singletons[name] = _build(name)
    return _singletons[name]


def list_backends() -> list[BackendStatus]:
    out: list[BackendStatus] = []
    for name in DEFAULT_PRIORITY:
        try:
            be = get_backend(name)
            ok, msg = be.health_check()
            out.append(BackendStatus(name=name, ok=ok, message=msg))
        except Exception as e:  # noqa: BLE001
            out.append(BackendStatus(name=name, ok=False, message=f"{type(e).__name__}: {e}"))
    return out


def select_backend(preferred: Optional[str] = None) -> Backend:
    """Return the first healthy backend, optionally honoring an explicit pick."""
    if preferred:
        be = get_backend(preferred)
        ok, msg = be.health_check()
        if not ok:
            log.warning("preferred backend %s unhealthy: %s", preferred, msg)
        return be
    statuses = list_backends()
    for s in statuses:
        if s.ok:
            return get_backend(s.name)
    raise RuntimeError(
        "no healthy backend; statuses:\n"
        + "\n".join(f"  {s.name}: {s.message}" for s in statuses)
    )
