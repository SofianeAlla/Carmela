"""FastAPI sidecar — sole HTTP surface for the Electron renderer.

Run standalone:
    python -m api.main --port 5174

Or let the Electron main process spawn it as a sidecar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("api")

from backends import AssetRequest, list_backends  # noqa: E402
from carla_io import (  # noqa: E402
    CarlaConnection,
    get_connection_status,
    list_blueprints,
    prepare_carla_import_package,
    spawn_static_prop,
    spawn_vehicle_by_blueprint,
)
from carla_io.package import PackageInput  # noqa: E402
from core import Orchestrator  # noqa: E402
from post_process import AVPostProcessConfig  # noqa: E402

app = FastAPI(title="CARMELA API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()
executor = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict] = {}


# ----- models -----

class ComplianceConfig(BaseModel):
    semantic_tag: Optional[str] = None
    pivot: Optional[str] = "ground-center"
    collision_shape: Optional[str] = "convex"
    lod_targets: list[int] = Field(default_factory=list)


class GenerateBody(BaseModel):
    prompt: str
    image_path: Optional[str] = None
    asset_class: str = "prop"
    quality: str = "standard"
    seed: int = 0
    polycount: Optional[int] = None
    target_height_m: Optional[float] = None
    backend: Optional[str] = None  # None = auto
    formats: list[str] = Field(default_factory=lambda: ["glb"])
    compliance: Optional[ComplianceConfig] = None


class BatchBody(BaseModel):
    items: list[GenerateBody]
    backend: Optional[str] = None


class SpawnBody(BaseModel):
    host: str = "localhost"
    port: int = 2000
    blueprint_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.5
    yaw_deg: float = 0.0
    as_vehicle: bool = False


class PackageBody(BaseModel):
    package_name: str = "carmela_pack"
    asset_class: Optional[str] = None
    asset_ids: list[str] = Field(default_factory=list)
    carla_root: Optional[str] = None
    remote_host: Optional[str] = None
    transport: str = "local"
    include_collision: bool = True
    include_lods: bool = False
    naming: str = "carla"


class BBoxBody(BaseModel):
    tight_min: list[float]
    tight_max: list[float]
    collision_min: list[float]
    collision_max: list[float]


# ----- helpers -----

def _to_request(body: GenerateBody) -> AssetRequest:
    return AssetRequest(
        prompt=body.prompt,
        image_path=body.image_path if body.image_path else None,
        asset_class=body.asset_class,
        quality=body.quality,
        seed=body.seed,
        polycount=body.polycount,
    )


def _run_job(job_id: str, body: GenerateBody) -> None:
    job = jobs[job_id]
    job["status"] = "running"
    job["started_at"] = time.time()
    job["progress"] = 0.0
    job["progress_msg"] = "Submitting…"

    def _on_progress(pct: float, msg: str | None = None) -> None:
        try:
            job["progress"] = float(pct)
            if msg:
                job["progress_msg"] = str(msg)
        except Exception:  # noqa: BLE001, S110
            pass

    try:
        req = _to_request(body)
        post_cfg = AVPostProcessConfig(target_height_m=body.target_height_m)
        # Resolve which backend will be used so we can attach the progress hook.
        from backends.registry import select_backend  # noqa: PLC0415

        be = select_backend(body.backend)
        be.progress_cb = _on_progress
        try:
            entry = orchestrator.generate_one(req, post_cfg=post_cfg, backend_name=be.name)
        finally:
            be.progress_cb = None
        job["progress"] = 100.0
        job["progress_msg"] = "Done"
        job["status"] = "completed"
        job["result"] = entry.model_dump()
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        job["trace"] = traceback.format_exc()[-2000:]
    finally:
        job["finished_at"] = time.time()


# ----- routes -----

@app.get("/health")
def health():
    return {"ok": True, "service": "carmela", "version": app.version}


@app.get("/backends/health")
def backends_health():
    return [{"name": s.name, "ok": s.ok, "message": s.message} for s in list_backends()]


@app.post("/uploads/image")
async def upload_image(file: UploadFile = File(...)):
    cache_dir = orchestrator.cache_dir / "uploads"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"{uuid.uuid4().hex}_{file.filename}"
    with open(dest, "wb") as f:
        f.write(await file.read())
    return {"path": str(dest), "name": file.filename}


@app.post("/generate")
def generate(body: GenerateBody):
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "id": job_id,
        "status": "pending",
        "request": body.model_dump(),
        "started_at": None,
        "finished_at": None,
    }
    executor.submit(_run_job, job_id, body)
    return {"job_id": job_id}


@app.post("/batch")
def batch(body: BatchBody):
    ids: list[str] = []
    for item in body.items:
        item.backend = item.backend or body.backend
        r = generate(item)
        ids.append(r["job_id"])
    return {"job_ids": ids}


@app.get("/jobs/{job_id}")
def job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "no such job")
    return jobs[job_id]


@app.get("/jobs")
def list_jobs():
    return list(jobs.values())


# ----- library -----

@app.get("/library")
def library_list(asset_class: Optional[str] = None):
    entries = orchestrator.library.list(asset_class=asset_class)
    return [e.model_dump() for e in entries]


@app.get("/library/stats")
def library_stats():
    return orchestrator.library.stats()


@app.delete("/library/{asset_id}")
def library_delete(asset_id: str):
    ok = orchestrator.library.remove(asset_id)
    return {"removed": ok}


@app.get("/library/{asset_id}/glb")
def library_glb(asset_id: str):
    for e in orchestrator.library.list():
        if e.asset_id == asset_id:
            return FileResponse(e.carla_glb, media_type="model/gltf-binary")
    raise HTTPException(404, "asset not in library")


@app.get("/library/{asset_id}/ply")
def library_ply(asset_id: str):
    for e in orchestrator.library.list():
        if e.asset_id == asset_id:
            ply = e.raw_response.get("ply_path") if isinstance(e.raw_response, dict) else None
            cand = ply or Path(e.carla_glb).with_suffix(".ply")
            if Path(cand).exists():
                return FileResponse(str(cand), media_type="application/octet-stream")
    raise HTTPException(404, "ply not available")


@app.post("/library/{asset_id}/bbox")
def library_set_bbox(asset_id: str, body: BBoxBody):
    """Save a tight + collision bbox into the asset's metadata sidecar."""
    for e in orchestrator.library.list():
        if e.asset_id == asset_id:
            meta_path = Path(e.metadata_path)
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
            meta["bbox_tight_m"] = [body.tight_min, body.tight_max]
            meta["bbox_collision_m"] = [body.collision_min, body.collision_max]
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return {"ok": True}
    raise HTTPException(404, "asset not in library")


@app.post("/library/package")
def library_package(body: PackageBody):
    if body.asset_ids:
        entries = [e for e in orchestrator.library.list() if e.asset_id in body.asset_ids]
    else:
        entries = orchestrator.library.list(asset_class=body.asset_class)
    inputs = [
        PackageInput(
            asset_id=e.asset_id,
            glb_path=e.carla_glb,
            asset_class=e.request.asset_class,
            metadata={
                "backend": e.backend,
                "naming": body.naming,
                "include_collision": body.include_collision,
                "include_lods": body.include_lods,
            },
        )
        for e in entries
    ]
    result = prepare_carla_import_package(
        inputs,
        package_name=body.package_name,
        carla_root=body.carla_root,
    )
    if body.transport != "local" and body.remote_host:
        result["transfer_command"] = (
            f'robocopy "{result["import_dir"]}" "{body.remote_host}" /E'
            if "@" not in body.remote_host
            else f'rsync -av "{result["import_dir"]}/" "{body.remote_host}/"'
        )
    return result


# ----- system -----

class BespokeKeyBody(BaseModel):
    api_key: str


@app.get("/system/bespoke-key")
def bespoke_key_status():
    """Reports whether a key is set, plus a masked preview. Never returns the raw key."""
    key = os.getenv("BESPOKE_API_KEY", "")
    if not key:
        return {"set": False, "preview": "", "valid": None}
    preview = (key[:6] + "…" + key[-4:]) if len(key) > 12 else key[:2] + "…"
    return {"set": True, "preview": preview, "valid": None}


@app.post("/system/bespoke-key")
def bespoke_key_save(body: BespokeKeyBody):
    """Persist the API key to .env, update os.environ + invalidate the BespokeBackend
    instance so the next /generate call picks it up immediately. The /backends/health
    poll right after this returns the new state."""
    key = body.api_key.strip()
    if key and not key.startswith("bspk_"):
        raise HTTPException(400, "BespokeAI keys start with 'bspk_'")
    env_path = ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    for i, ln in enumerate(lines):
        if ln.startswith("BESPOKE_API_KEY="):
            lines[i] = f"BESPOKE_API_KEY={key}"
            found = True
            break
    if not found:
        lines.append(f"BESPOKE_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["BESPOKE_API_KEY"] = key
    # Invalidate the backend singleton so the new key takes effect.
    from backends.registry import _singletons  # noqa: PLC0415
    _singletons.pop("bespoke_api", None)
    return {"set": bool(key), "preview": (key[:6] + "…" + key[-4:]) if len(key) > 12 else key}


@app.post("/system/bespoke-key/test")
def bespoke_key_test():
    """Run a cheap probe against the Bespoke health endpoint with the current key."""
    from backends.registry import get_backend  # noqa: PLC0415
    try:
        be = get_backend("bespoke_api")
        ok, msg = be.health_check()
        return {"ok": ok, "message": msg}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


# ====== TRELLIS.2 status (read-only — install is via scripts/install.ps1) ======

_EXT_NAMES = ("cumesh", "flex_gemm", "o_voxel", "nvdiffrast", "nvdiffrec_render")


def _check_trellis_extensions() -> dict[str, str]:
    """Probe the trellis2 venv for the 5 native extensions."""
    venv_py = ROOT.parent / "external" / ".venv-trellis2" / "Scripts" / "python.exe"
    if not venv_py.exists():
        return {n: "missing" for n in _EXT_NAMES}
    probe = (
        "import importlib.util, json;"
        "print(json.dumps({n: ('ok' if importlib.util.find_spec(n) else 'missing') "
        f"for n in {list(_EXT_NAMES)!r}}}))"
    )
    try:
        out = subprocess.check_output([str(venv_py), "-c", probe], text=True, timeout=15)
        return json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {n: "missing" for n in _EXT_NAMES}


@app.get("/system/trellis2/status")
def trellis2_status():
    repo = ROOT.parent / "external" / "TRELLIS.2"
    venv = ROOT.parent / "external" / ".venv-trellis2"
    exts = _check_trellis_extensions()
    return {
        "repo_present": repo.exists(),
        "venv_present": venv.exists(),
        "extensions": exts,
        "ready": all(v == "ok" for v in exts.values()),
    }


@app.get("/system/env")
def system_env():
    keys = [
        "CARLA_ROOT", "CARLA_HOST", "CARLA_PORT",
        "COMFYUI_HOST", "COMFYUI_PORT", "COMFYUI_WORKFLOW",
        "BESPOKE_API_KEY", "BESPOKE_API_URL",
        "TRELLIS2_REPO", "TRELLIS2_CKPT",
        "ASSET_LIBRARY_DIR", "ASSET_CACHE_DIR",
    ]
    masked = {"BESPOKE_API_KEY", "HUGGINGFACE_TOKEN"}
    out = {}
    for k in keys:
        v = os.getenv(k, "")
        if k in masked and v:
            v = v[:4] + "…" + v[-4:]
        out[k] = v
    return out




# ----- carla -----

@app.get("/carla/status")
def carla_status(host: str = "localhost", port: int = 2000):
    return get_connection_status(host, port)


@app.get("/carla/blueprints")
def carla_blueprints(filter_prefix: str = "", host: str = "localhost", port: int = 2000):
    try:
        conn = CarlaConnection(host=host, port=port)
        return {"ids": list_blueprints(filter_prefix, conn=conn)}
    except Exception as e:  # noqa: BLE001
        return {"ids": [], "error": str(e)}


@app.post("/carla/spawn")
def carla_spawn(body: SpawnBody):
    conn = CarlaConnection(host=body.host, port=body.port)
    try:
        if body.as_vehicle:
            return spawn_vehicle_by_blueprint(body.blueprint_id, conn=conn)
        return spawn_static_prop(
            body.blueprint_id,
            (body.x, body.y, body.z),
            (0.0, body.yaw_deg, 0.0),
            conn=conn,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("API_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "5174")))
    args = ap.parse_args()

    import uvicorn  # noqa: PLC0415

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
