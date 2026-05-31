"""Thin wrapper around carla.Client with friendly errors and connection caching."""
from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


def _import_carla():
    try:
        import carla  # noqa: PLC0415

        return carla
    except ImportError as e:
        raise RuntimeError(
            "carla python module not installed. "
            "Run: pip install carla==0.9.16 (or use the venv that has it)."
        ) from e


@dataclass
class CarlaConnection:
    host: str = "localhost"
    port: int = 2000
    timeout_s: float = 5.0
    _client: Optional[object] = None

    def client(self):
        carla = _import_carla()
        if self._client is None:
            self._client = carla.Client(self.host, self.port)
            self._client.set_timeout(self.timeout_s)
        return self._client

    def world(self):
        return self.client().get_world()

    def server_version(self) -> str:
        return self.client().get_server_version()

    def client_version(self) -> str:
        return self.client().get_client_version()

    def is_alive(self) -> bool:
        with contextlib.suppress(Exception):
            self.server_version()
            return True
        return False


def get_connection_status(host: Optional[str] = None, port: Optional[int] = None) -> dict:
    host = host or os.getenv("CARLA_HOST", "localhost")
    port = port or int(os.getenv("CARLA_PORT", "2000"))
    conn = CarlaConnection(host=host, port=port, timeout_s=2.0)
    info = {"host": host, "port": port, "ok": False, "client_version": None, "server_version": None, "error": None}
    try:
        info["client_version"] = conn.client_version()
        info["server_version"] = conn.server_version()
        info["ok"] = True
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
    return info


def list_blueprints(filter_prefix: str = "", conn: Optional[CarlaConnection] = None) -> list[str]:
    conn = conn or CarlaConnection()
    world = conn.world()
    blueprints = world.get_blueprint_library()
    if filter_prefix:
        blueprints = blueprints.filter(filter_prefix + "*")
    return sorted([bp.id for bp in blueprints])
