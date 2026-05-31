"""BespokeAI backend — wraps the user's own bespokeai.build API.

Real endpoint (from the ComfyUI-BespokeAI-3D node we ship as the reference):

    POST https://heovujhdxkvbkaaguzwl.supabase.co/functions/v1/public-3d-api
        X-API-Key: bspk_...
        { imageData (base64 dataURL), resolution, withTexture,
          aiEnhancement, lowPoly, segmentation, prompt }
        -> { taskId }

    GET https://heovujhdxkvbkaaguzwl.supabase.co/functions/v1/public-3d-api?taskId=...
        X-API-Key: bspk_...
        -> { status: complete|processing|failed,
             progress: 0..100,  (when processing)
             glbUrl, objUrl, modelUrl, ... } (when complete)
"""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .base import (
    AssetRequest,
    AssetResult,
    Backend,
    BackendError,
    BackendUnavailable,
)

log = logging.getLogger(__name__)

API_URL = "https://heovujhdxkvbkaaguzwl.supabase.co/functions/v1/public-3d-api"


class BespokeBackend(Backend):
    name = "bespoke_api"
    requires_internet = True
    supports_image = True
    supports_text_only = False

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("BESPOKE_API_KEY") or ""
        # `base_url` is kept for env override; defaults to the Supabase endpoint.
        self.base_url = (base_url or os.getenv("BESPOKE_API_URL") or API_URL).rstrip("/")

    def _headers(self, ct_json: bool = True) -> dict:
        h = {"X-API-Key": self.api_key}
        if ct_json:
            h["Content-Type"] = "application/json"
        return h

    def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "BESPOKE_API_KEY not set"
        if not self.api_key.startswith("bspk_"):
            return False, "API key should start with 'bspk_'"
        # Probe with a GET — server will reject without a taskId, which is fine.
        # 401 means bad key, 400 means good key but bad request (= reachable).
        try:
            r = requests.get(self.base_url, headers=self._headers(ct_json=False), timeout=8)
        except requests.RequestException as e:
            return False, f"unreachable: {type(e).__name__}: {e}"
        if r.status_code == 401:
            return False, "invalid API key (401)"
        if r.status_code == 402:
            return False, "no credits remaining (402)"
        if r.status_code in (200, 400, 422):
            # Endpoint responded; key is at least syntactically valid.
            preview = self.api_key[:6] + "…" + self.api_key[-4:]
            return True, f"connected · {preview}"
        return False, f"unexpected HTTP {r.status_code}: {r.text[:120]}"

    # ----- generation -----

    @staticmethod
    def _image_to_data_url(path: str) -> str:
        raw = Path(path).read_bytes()
        ext = Path(path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp"}.get(ext, "image/png")
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _quality_to_resolution(quality: str) -> str:
        return {"draft": "500k", "standard": "1m", "hero": "1.5m"}.get(quality, "1m")

    def _poll(self, task_id: str, max_wait_s: int = 600, interval_s: float = 5.0) -> dict:
        deadline = time.time() + max_wait_s
        last_progress = -1
        # Bespoke's progress is generation-only; reserve 0–5% for submit and
        # ~95–100% for download, so the UI bar feels continuous.
        if self.progress_cb:
            try: self.progress_cb(5.0, "Submitted · processing…")
            except Exception: pass  # noqa: E722
        while time.time() < deadline:
            try:
                r = requests.get(
                    self.base_url,
                    headers=self._headers(ct_json=False),
                    params={"taskId": task_id},
                    timeout=20,
                )
            except requests.RequestException as e:
                log.warning("poll error (retrying): %s", e)
                time.sleep(interval_s)
                continue
            if not r.ok:
                err = r.json().get("error", r.text[:200]) if r.text else "no body"
                raise BackendError(f"poll failed {r.status_code}: {err}")
            data = r.json()
            status = data.get("status", "unknown")
            if status == "complete":
                if self.progress_cb:
                    try: self.progress_cb(95.0, "Downloading GLB…")
                    except Exception: pass  # noqa: E722
                return data
            if status in ("failed", "error"):
                raise BackendError(f"Bespoke job failed: {data.get('error', data)}")
            prog = data.get("progress", 0)
            if prog != last_progress:
                log.info("bespoke progress: %s%% (status=%s)", prog, status)
                last_progress = prog
                if self.progress_cb:
                    # Map 0..100 of generation to 5..95 of overall.
                    overall = 5.0 + (max(0, min(100, prog)) * 0.9)
                    try: self.progress_cb(overall, f"Generating · {prog}%")
                    except Exception: pass  # noqa: E722
            time.sleep(interval_s)
        raise BackendError(f"Bespoke job {task_id} timed out after {max_wait_s}s")

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        if not self.api_key:
            raise BackendUnavailable("BESPOKE_API_KEY missing")
        if not req.image_path or not Path(req.image_path).exists():
            raise BackendError(
                "Bespoke is image-to-3D; supply image_path. "
                "Generate one via a text-to-image step first."
            )

        t0 = time.time()
        submit_resp = self._submit_full(req)
        task_id = submit_resp["taskId"]
        credits_used = submit_resp.get("creditsUsed", 0)
        log.info("Bespoke job submitted: task=%s credits=%s", task_id, credits_used)
        result = self._poll(task_id)
        # Mirrors blender-bespokeai-3d's URL extraction order:
        #   1. result.resultFiles[] where Type == 'glb' → Url
        #   2. result.modelUrl as fallback
        glb_url = ""
        for f in result.get("resultFiles", []) or []:
            if isinstance(f, dict) and (f.get("Type") or "").lower() == "glb":
                glb_url = f.get("Url", "") or ""
                if glb_url:
                    break
        if not glb_url:
            glb_url = result.get("modelUrl") or ""
        if not glb_url:
            raise BackendError(f"no GLB url in completed task: {result}")

        out_path = out_dir / f"{req.cache_key()}.glb"
        with requests.get(glb_url, stream=True, timeout=180) as g:
            g.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in g.iter_content(1 << 16):
                    f.write(chunk)

        return AssetResult(
            request=req,
            glb_path=str(out_path),
            backend=self.name,
            duration_s=time.time() - t0,
            raw_response={
                "task_id": task_id,
                "credits_used": credits_used,
                "result_files": result.get("resultFiles"),
            },
        )

    def _submit_full(self, req: AssetRequest) -> dict:
        """Same as _submit but returns the full submit response (taskId + creditsUsed)."""
        data_url = self._image_to_data_url(req.image_path)
        payload = {
            "imageData": data_url,
            "resolution": self._quality_to_resolution(req.quality),
            "withTexture": req.pbr,
            "aiEnhancement": True,
            "lowPoly": req.quality == "draft",
            "segmentation": False,
        }
        if req.prompt:
            payload["prompt"] = req.prompt
        r = requests.post(self.base_url, headers=self._headers(), json=payload, timeout=60)
        if r.status_code == 401:
            raise BackendError("Bespoke 401: invalid API key")
        if r.status_code == 402:
            raise BackendError("Bespoke 402: insufficient credits")
        if r.status_code == 429:
            raise BackendError("Bespoke 429: rate limit exceeded")
        if r.status_code in (400, 422):
            try:
                err = r.json()
            except Exception:  # noqa: BLE001
                err = {"error": r.text[:200]}
            raise BackendError(f"Bespoke {r.status_code}: {err.get('code', 'ERROR')}: {err.get('error', 'unknown')}")
        if not r.ok:
            raise BackendError(f"Bespoke submit failed {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "taskId" not in data:
            raise BackendError(f"no taskId in response: {data}")
        return data
