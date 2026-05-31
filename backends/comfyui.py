"""ComfyUI backend — talks to a local ComfyUI server via its HTTP API.

This is the user's primary path. ComfyUI is already installed (ComfyUI Desktop
at C:\\Users\\allas\\AppData\\Local\\Programs\\ComfyUI\\) and the BespokeAI 3D
custom node is at OneDrive\\...\\R&D\\ComfyUI-BespokeAI-3D. The custom node
runs against bespokeai.build under the hood, so this path equals
"BespokeAI via ComfyUI workflow" — which lets you extend the pipeline with
ComfyUI's other nodes (preprocess, segment, post-process) without touching
this code.

API contract used (ComfyUI 0.4+):
  POST /upload/image           multipart, returns {name}
  POST /prompt                 {prompt: <api-format-json>, client_id}
  GET  /history/{prompt_id}    poll until 'status.completed' is True
  GET  /view?filename=...&subfolder=...&type=output

For a different 3D node (e.g. ComfyUI-3D-Pack TRELLIS), pass workflow_path
to the backend and we'll patch its image input slot.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import time
import urllib.parse
import uuid
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

# Default api-format workflow: LoadImage -> BespokeAI3DGeneration.
# This is the API-format (flat dict keyed by node id), NOT the UI .json
# that the user has in workflows/. We build it programmatically so we don't
# need to ship a copy.
DEFAULT_BESPOKE_WORKFLOW = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": "__INPUT__", "upload": "image"},
    },
    "2": {
        "class_type": "BespokeAI3DGeneration",
        "inputs": {
            "image": ["1", 0],
            "api_key": "",
            "resolution": "1m",
            "with_texture": True,
            "ai_enhancement": True,
            "low_poly": False,
            "segmentation": False,
            "prompt": "",
            "poll_interval": 5.0,
            "max_poll_attempts": 120,
        },
    },
}


class ComfyUIBackend(Backend):
    name = "comfyui"
    requires_internet = False  # only local HTTP; the BespokeAI node hits the net itself
    supports_image = True
    supports_text_only = False  # ComfyUI workflows here are image-to-3D

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        workflow_path: Optional[str] = None,
        node_id_image: str = "1",
        node_id_generator: str = "2",
        output_subfolder: str = "bespokeai_3d",
        api_key: Optional[str] = None,
        timeout_s: int = 600,
    ):
        self.host = host or os.getenv("COMFYUI_HOST", "127.0.0.1")
        self.port = port or int(os.getenv("COMFYUI_PORT", "8188"))
        self.workflow_path = workflow_path or os.getenv("COMFYUI_WORKFLOW") or None
        self.node_id_image = node_id_image
        self.node_id_generator = node_id_generator
        self.output_subfolder = output_subfolder
        self.api_key = api_key or os.getenv("BESPOKE_API_KEY", "")
        self.timeout_s = timeout_s
        self.client_id = uuid.uuid4().hex

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def health_check(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=2)
            if r.status_code == 200:
                stats = r.json()
                dev = stats.get("devices", [{}])[0].get("name", "?")
                return True, f"ready · {dev}"
            return False, f"http {r.status_code}"
        except requests.RequestException as e:
            return False, f"server not reachable on {self.base_url}: {type(e).__name__}"

    # ----- helpers -----

    def _load_workflow(self) -> dict:
        if not self.workflow_path:
            return copy.deepcopy(DEFAULT_BESPOKE_WORKFLOW)
        with open(self.workflow_path, encoding="utf-8") as f:
            wf = json.load(f)
        # Accept either UI-format (with "nodes" array) or API-format (flat dict).
        if "nodes" in wf:
            wf = _ui_workflow_to_api(wf)
        return wf

    def _upload_image(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            files = {"image": (Path(image_path).name, f, "image/png")}
            data = {"type": "input", "subfolder": "", "overwrite": "true"}
            r = requests.post(f"{self.base_url}/upload/image", files=files, data=data, timeout=30)
            r.raise_for_status()
            return r.json()["name"]

    def _queue_prompt(self, workflow: dict) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        r = requests.post(f"{self.base_url}/prompt", json=payload, timeout=15)
        if r.status_code != 200:
            raise BackendError(f"ComfyUI /prompt error {r.status_code}: {r.text[:400]}")
        data = r.json()
        if "prompt_id" not in data:
            raise BackendError(f"no prompt_id in /prompt response: {data}")
        return data["prompt_id"]

    def _wait_history(self, prompt_id: str) -> dict:
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if prompt_id in data:
                        entry = data[prompt_id]
                        status = entry.get("status", {})
                        if status.get("completed"):
                            return entry
                        if status.get("status_str") == "error":
                            err_msgs = []
                            for m in status.get("messages", []):
                                if isinstance(m, list) and len(m) >= 2:
                                    err_msgs.append(str(m[1])[:200])
                            raise BackendError("ComfyUI workflow error: " + " | ".join(err_msgs))
            except requests.RequestException as e:
                log.debug("history poll error: %s", e)
            time.sleep(2.0)
        raise BackendError(f"ComfyUI prompt {prompt_id} timed out after {self.timeout_s}s")

    def _find_output_glb(self, history_entry: dict) -> Optional[dict]:
        """Walk outputs to find a .glb produced by the BespokeAI node.

        Returns a dict {filename, subfolder, type} or None.
        """
        outputs = history_entry.get("outputs", {})
        # Most generators return text strings (the GLB path) — try those first.
        for node_id, node_out in outputs.items():
            # paths returned by the node code
            for key in ("ui", "images", "files", "result"):
                items = node_out.get(key)
                if not items:
                    continue
                if isinstance(items, dict):
                    items = [items]
                for it in items:
                    if isinstance(it, dict):
                        fn = it.get("filename") or it.get("name") or ""
                        if fn.endswith(".glb"):
                            return {
                                "filename": fn,
                                "subfolder": it.get("subfolder", self.output_subfolder),
                                "type": it.get("type", "output"),
                            }
                    elif isinstance(it, str) and it.endswith(".glb"):
                        # Plain string path — assume file is under output dir.
                        p = Path(it)
                        return {
                            "filename": p.name,
                            "subfolder": p.parent.name if p.parent.name != "output" else self.output_subfolder,
                            "type": "output",
                        }
        return None

    def _download(self, ref: dict, dest: Path) -> None:
        params = urllib.parse.urlencode(ref)
        url = f"{self.base_url}/view?{params}"
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)

    # ----- public -----

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        ok, msg = self.health_check()
        if not ok:
            raise BackendUnavailable(f"comfyui: {msg}")
        if not req.image_path or not Path(req.image_path).exists():
            raise BackendError(
                "ComfyUI Trellis workflow is image-to-3D; supply image_path. "
                "Use a text-to-image step first if you only have a prompt."
            )

        t0 = time.time()
        uploaded = self._upload_image(req.image_path)

        workflow = self._load_workflow()
        # Patch the image input + API key on the well-known node ids.
        if self.node_id_image in workflow:
            workflow[self.node_id_image]["inputs"]["image"] = uploaded
        if self.node_id_generator in workflow:
            gen_inputs = workflow[self.node_id_generator]["inputs"]
            if self.api_key and "api_key" in gen_inputs:
                gen_inputs["api_key"] = self.api_key
            if "prompt" in gen_inputs and req.prompt:
                gen_inputs["prompt"] = req.prompt
            # Map our quality dial to BespokeAI resolutions.
            qmap = {"draft": "500k", "standard": "1m", "hero": "1.5m"}
            if "resolution" in gen_inputs:
                gen_inputs["resolution"] = qmap.get(req.quality, "1m")
            if "low_poly" in gen_inputs and req.quality == "draft":
                gen_inputs["low_poly"] = True

        prompt_id = self._queue_prompt(workflow)
        log.info("ComfyUI prompt queued: %s", prompt_id)
        entry = self._wait_history(prompt_id)

        ref = self._find_output_glb(entry)
        if not ref:
            # Sometimes the BespokeAI node downloads to disk and returns a path
            # string. Re-scan local output dir as last resort.
            local_path = _scan_local_output(entry, self.output_subfolder)
            if local_path:
                out_path = out_dir / f"{req.cache_key()}.glb"
                shutil.copy(local_path, out_path)
                return AssetResult(
                    request=req,
                    glb_path=str(out_path),
                    backend=self.name,
                    duration_s=time.time() - t0,
                    raw_response={"prompt_id": prompt_id, "source": "local"},
                )
            raise BackendError(f"no GLB in workflow outputs for {prompt_id}: {entry.get('outputs', {})}")

        out_path = out_dir / f"{req.cache_key()}.glb"
        self._download(ref, out_path)

        return AssetResult(
            request=req,
            glb_path=str(out_path),
            backend=self.name,
            duration_s=time.time() - t0,
            raw_response={"prompt_id": prompt_id, "ref": ref},
        )


# ---------- module-level helpers ----------

def _ui_workflow_to_api(ui_wf: dict) -> dict:
    """Convert a UI-format workflow JSON to ComfyUI's flat API format.
    Minimal best-effort implementation — covers the BespokeAI 3D template.
    """
    out: dict = {}
    nodes = {n["id"]: n for n in ui_wf.get("nodes", [])}
    # Build a (src_id, slot) -> link_id index then a link_id -> (dst_id, dst_slot, src_id, src_slot, type)
    links_by_id = {l[0]: l for l in ui_wf.get("links", [])}
    for nid, node in nodes.items():
        inputs: dict = {}
        for inp in node.get("inputs", []) or []:
            link_id = inp.get("link")
            if link_id is None:
                continue
            link = links_by_id.get(link_id)
            if not link:
                continue
            _, src_id, src_slot, _, _, _ = link
            inputs[inp["name"]] = [str(src_id), int(src_slot)]
        widgets = node.get("widgets_values") or []
        # Heuristic: widgets are usually image filename / number / bool entries.
        # We map them by their position in the node's widget order — caller may
        # override later.
        if node.get("type") == "LoadImage" and widgets:
            inputs["image"] = widgets[0]
            inputs["upload"] = "image"
        out[str(nid)] = {
            "class_type": node["type"],
            "inputs": inputs,
        }
    return out


def _scan_local_output(entry: dict, subfolder: str) -> Optional[str]:
    # The BespokeAI node sometimes writes to ComfyUI's output dir directly and
    # only logs the path. Look for the newest .glb under <comfy_output>/<subfolder>.
    candidates: list[str] = []
    for nid, node_out in entry.get("outputs", {}).items():
        for v in node_out.values():
            if isinstance(v, str) and v.endswith(".glb") and Path(v).exists():
                candidates.append(v)
    if candidates:
        candidates.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
        return candidates[0]
    return None
