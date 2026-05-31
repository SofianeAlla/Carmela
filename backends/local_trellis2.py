"""Local TRELLIS.2 backend (subprocess-based).

Spawns external/run_trellis2.py inside the dedicated trellis2 venv at
external/.venv-trellis2. This isolates TRELLIS.2's heavy torch CUDA 12.4
install from our slim FastAPI sidecar venv.

The backend supports both GLB and PLY outputs. On 8GB-VRAM laptops, the
script auto-falls back to fp16 + sequential CPU offload (set quality='draft'
to hit resolution=512 which fits).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

# json is used inside health_check (subprocess probe parses JSON).

from .base import (
    AssetRequest,
    AssetResult,
    Backend,
    BackendError,
    BackendUnavailable,
)

log = logging.getLogger(__name__)


def _project_root() -> Path:
    # backends/local_trellis2.py → trellis_carla/ → parent is repo root
    return Path(__file__).resolve().parent.parent.parent


def _trellis2_repo() -> Optional[Path]:
    env = os.getenv("TRELLIS2_REPO")
    if env:
        p = Path(env)
        return p if p.exists() else None
    default = _project_root() / "external" / "TRELLIS.2"
    return default if default.exists() else None


def _trellis2_python() -> Optional[Path]:
    env = os.getenv("TRELLIS2_PYTHON")
    if env and Path(env).exists():
        return Path(env)
    default = _project_root() / "external" / ".venv-trellis2" / "Scripts" / "python.exe"
    return default if default.exists() else None


def _runner_script() -> Path:
    return _project_root() / "external" / "run_trellis2.py"


class LocalTrellis2Backend(Backend):
    name = "local_trellis2"
    requires_internet = False  # but first-run model download does need it
    supports_image = True
    supports_text_only = False

    def health_check(self) -> tuple[bool, str]:
        repo = _trellis2_repo()
        if not repo:
            return False, "external/TRELLIS.2 not cloned (run scripts/install_trellis2.ps1)"
        py = _trellis2_python()
        if not py:
            return False, "external/.venv-trellis2 not built yet"
        runner = _runner_script()
        if not runner.exists():
            return False, f"runner missing: {runner}"
        # Probe torch + GPU + the native extensions. We do all three in one
        # subprocess so we only pay the cold-start cost once.
        probe = (
            "import sys, json, importlib, importlib.util;"
            "out = {};"
            "import torch;"
            "out['torch'] = torch.__version__;"
            "p = torch.cuda.get_device_properties(0) if torch.cuda.is_available() else None;"
            "out['cuda'] = bool(p);"
            "out['gpu'] = p.name if p else None;"
            "out['vram_gb'] = round(p.total_memory/1e9, 1) if p else 0;"
            "sys.path.insert(0, r'" + str(repo) + "');"
            "out['exts'] = {n: importlib.util.find_spec(n) is not None for n in ['cumesh','flex_gemm','o_voxel']};"
            "print(json.dumps(out))"
        )
        try:
            raw = subprocess.check_output([str(py), "-c", probe], text=True, timeout=20).strip()
        except subprocess.SubprocessError as e:
            return False, f"trellis2 venv broken: {e}"
        try:
            info = json.loads(raw.splitlines()[-1])
        except Exception:  # noqa: BLE001
            return False, f"probe parse failed: {raw[:200]}"
        if not info.get("cuda"):
            return False, "no CUDA GPU in trellis2 venv"
        missing = [n for n, ok in info["exts"].items() if not ok]
        if missing:
            return False, (
                f"native extensions missing: {', '.join(missing)} · "
                f"install CUDA Toolkit 12.4 then run scripts/build_trellis2_native.ps1"
            )
        # DINOv3 image encoder is gated; warn if missing.
        dinov3_root = Path.home() / ".cache" / "huggingface" / "hub" / "models--facebook--dinov3-vitl16-pretrain-lvd1689m"
        dinov3_safetensors = list((dinov3_root / "snapshots").glob("*/model.safetensors")) if (dinov3_root / "snapshots").exists() else []
        if not dinov3_safetensors:
            return False, (
                "DINOv3 ViT-L/16 (gated) missing. Accept the license at "
                "https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m then run: "
                "huggingface-cli login && huggingface-cli download facebook/dinov3-vitl16-pretrain-lvd1689m"
            )
        vram = info["vram_gb"]
        msg = f"ready · {info['gpu']} · {vram:.1f}GB VRAM"
        if vram < 24:
            msg += " · low-VRAM mode (fp16+offload)"
        return True, msg

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        if not req.image_path or not Path(req.image_path).exists():
            raise BackendError("TRELLIS.2 is image-to-3D; supply image_path")
        ok, msg = self.health_check()
        if not ok:
            raise BackendUnavailable(msg)

        py = _trellis2_python()
        runner = _runner_script()
        repo = _trellis2_repo()

        out_glb = out_dir / f"{req.cache_key()}.glb"
        out_ply = out_dir / f"{req.cache_key()}.ply"
        out_dir.mkdir(parents=True, exist_ok=True)

        # quality → resolution.
        res_map = {"draft": 512, "standard": 1024, "hero": 1536}
        resolution = res_map.get(req.quality, 1024)

        cmd = [
            str(py),
            str(runner),
            "--image", str(req.image_path),
            "--out_glb", str(out_glb),
            "--out_ply", str(out_ply),
            "--resolution", str(resolution),
            "--seed", str(req.seed),
            "--trellis2_repo", str(repo),
        ]
        # Force low_vram for anything <=12GB; let user opt out via env.
        try:
            vram_gb = float(self.health_check()[1].split("·")[-1].split("GB")[0].strip())
        except Exception:  # noqa: BLE001
            vram_gb = 8.0
        if vram_gb < 16 or os.getenv("TRELLIS2_LOW_VRAM", "1") == "1":
            cmd.append("--low_vram")

        if req.polycount:
            cmd.extend(["--polycount", str(req.polycount)])

        log.info("trellis2 subprocess: %s", " ".join(cmd))
        t0 = time.time()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        events: list[dict] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                events.append(ev)
                log.info("trellis2: %s", ev)
                if ev.get("event") == "fatal":
                    proc.wait(5)
                    raise BackendError(
                        f"trellis2 failed: {ev.get('error')}: {ev.get('hint', '')} :: {ev.get('detail', '')[:300]}"
                    )
            except json.JSONDecodeError:
                log.info("trellis2: %s", line)
        rc = proc.wait()
        if rc != 0:
            raise BackendError(f"trellis2 exited rc={rc}")
        if not out_glb.exists():
            raise BackendError("trellis2 did not produce a GLB")

        return AssetResult(
            request=req,
            glb_path=str(out_glb),
            backend=self.name,
            duration_s=time.time() - t0,
            raw_response={
                "ply_path": str(out_ply) if out_ply.exists() else None,
                "events": events[-10:],
                "resolution": resolution,
            },
        )
