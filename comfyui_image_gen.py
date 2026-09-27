"""ComfyUIのローカルAPIを使った画像生成クライアント。"""

from __future__ import annotations

import copy
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class ComfyUIConfigurationError(ValueError):
    """ComfyUIまたはworkflow設定が不正。"""


class ComfyUIConnectionError(ConnectionError):
    """ComfyUIへ接続できない。"""


class ComfyUITimeoutError(TimeoutError):
    """ComfyUIの処理がタイムアウトした。"""


class ComfyUIError(RuntimeError):
    """ComfyUIの処理に失敗した。"""


@dataclass(frozen=True)
class GeneratedImage:
    path: Path
    seed: int


SDXL_REQUIRED_NODES = {
    "4": "CheckpointLoaderSimple",
    "5": "KSampler",
    "6": "CLIPTextEncode",
    "7": "CLIPTextEncode",
    "8": "EmptyLatentImage",
    "9": "VAEDecode",
    "10": "SaveImage",
}

SDXL_INJECTIONS = {
    "model": {"node": "4", "input": "ckpt_name"},
    "positive_prompt": {"node": "6", "input": "text"},
    "negative_prompt": {"node": "7", "input": "text"},
    "seed": {"node": "5", "input": "seed"},
    "steps": {"node": "5", "input": "steps"},
    "cfg": {"node": "5", "input": "cfg"},
    "sampler": {"node": "5", "input": "sampler_name"},
    "scheduler": {"node": "5", "input": "scheduler"},
    "width": {"node": "8", "input": "width"},
    "height": {"node": "8", "input": "height"},
    "batch_size": {"node": "8", "input": "batch_size"},
    "clip_skip": {
        "node": "11",
        "input": "stop_at_clip_layer",
        "connections": [
            {"node": "6", "input": "clip"},
            {"node": "7", "input": "clip"},
        ],
    },
}

WORKFLOW_CONFIGS = {
    "text2image_api_workflow.json": {
        "required_nodes": SDXL_REQUIRED_NODES,
        "injections": SDXL_INJECTIONS,
    },
    "qwen_image_2_1_t2i_api_workflow.json": {
        "required_nodes": {
            "1": "UNETLoader",
            "2": "CLIPLoader",
            "3": "VAELoader",
            "4": "TextEncodeQwenImage21",
            "5": "EmptyLatentImage",
            "6": "KSampler",
            "7": "VAEDecode",
            "8": "QwenImage21Cache",
            "9": "SaveImageAdvanced",
        },
        "injections": {
            "positive_prompt": {"node": "4", "input": "prompt"},
            "negative_prompt": {"node": "4", "input": "negative_prompt"},
            "seed": {"node": "6", "input": "seed"},
            "steps": {"node": "6", "input": "steps"},
            "cfg": {"node": "6", "input": "cfg"},
            "sampler": {"node": "6", "input": "sampler_name"},
            "scheduler": {"node": "6", "input": "scheduler"},
            "width": {"node": "5", "input": "width"},
            "height": {"node": "5", "input": "height"},
            "batch_size": {"node": "5", "input": "batch_size"},
            "model_files": {
                "diffusion_models": {"node": "1", "input": "unet_name"},
                "text_encoders": {"node": "2", "input": "clip_name"},
                "vae": {"node": "3", "input": "vae_name"},
            },
        },
    },
    "qwen_image_2_1_viggle_turbo_api_workflow.json": {
        "required_nodes": {
            "1": "UNETLoader",
            "2": "CLIPLoader",
            "3": "VAELoader",
            "4": "TextEncodeQwenImage21",
            "5": "EmptyLatentImage",
            "10": "ViggleTurboLora",
            "11": "ViggleTurboSigmas",
            "12": "BasicGuider",
            "13": "KSamplerSelect",
            "14": "RandomNoise",
            "15": "SamplerCustomAdvanced",
            "7": "VAEDecode",
            "9": "SaveImage",
        },
        "injections": {
            "positive_prompt": {"node": "4", "input": "prompt"},
            "seed": {"node": "14", "input": "noise_seed"},
            "width": {"node": "5", "input": "width"},
            "height": {"node": "5", "input": "height"},
            "model_files": {
                "diffusion_models": {"node": "1", "input": "unet_name"},
                "text_encoders": {"node": "2", "input": "clip_name"},
                "vae": {"node": "3", "input": "vae_name"},
                "loras": {"node": "10", "input": "lora_name"},
            },
        },
    },
    "qwen_image_2_1_viggle_turbo_turnaround_api_workflow.json": {
        "required_nodes": {
            "1": "UNETLoader", "2": "CLIPLoader", "3": "VAELoader",
            "5": "EmptyLatentImage", "10": "ViggleTurboLora",
            "11": "ViggleTurboSigmas", "12": "BasicGuider",
            "13": "KSamplerSelect", "14": "RandomNoise",
            "15": "SamplerCustomAdvanced", "18": "TextEncodeQwenImage21",
            "26": "LoadImage", "29": "QwenImage21Cache",
            "7": "VAEDecode", "9": "SaveImage",
        },
        "injections": {
            "positive_prompt": {"node": "18", "input": "prompt"},
            "reference_image": {"node": "26", "input": "image"},
            "seed": {"node": "14", "input": "noise_seed"},
            "width": {"node": "5", "input": "width"},
            "height": {"node": "5", "input": "height"},
            "model_files": {
                "diffusion_models": {"node": "1", "input": "unet_name"},
                "text_encoders": {"node": "2", "input": "clip_name"},
                "vae": {"node": "3", "input": "vae_name"},
                "loras": {"node": "10", "input": "lora_name"},
            },
        },
    },
}


class ComfyUIImageGenerator:
    """ComfyUI API workflowを使って画像を生成する。"""

    # Kept as a public compatibility alias for callers that used the old SDXL map.
    REQUIRED_NODES = SDXL_REQUIRED_NODES

    def __init__(
        self,
        base_url: str,
        workflow_path: Path,
        checkpoint_name: str,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 1.0,
        width: int = 768,
        height: int = 1152,
        steps: int = 24,
        cfg: float = 7.0,
        sampler: str = "euler",
        scheduler: str = "normal",
        clip_skip: Optional[int] = None,
        model_files: Optional[list[Any]] = None,
        negative_prompt: str = (
            "low quality, blurry, distorted hands, extra fingers, cropped, duplicate"
        ),
        opener: Optional[Callable[..., Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        seed_factory: Callable[[], int] = lambda: secrets.randbelow(2**63),
        turnaround_workflow_path: Optional[Path] = None,
        turnaround_width: Optional[int] = None,
        turnaround_height: Optional[int] = None,
        turnaround_timeout_seconds: Optional[float] = None,
    ):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ComfyUIConfigurationError(
                "COMFYUI_URL must point to a local HTTP server "
                "(127.0.0.1, localhost, or ::1)."
            )
        if checkpoint_name == "CHANGE_ME.safetensors":
            raise ComfyUIConfigurationError(
                "Set COMFYUI_CHECKPOINT_NAME to an installed checkpoint filename."
            )

        self.base_url = base_url.rstrip("/")
        self.workflow_path = Path(workflow_path)
        self.checkpoint_name = checkpoint_name
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.width = width
        self.height = height
        self.steps = steps
        self.cfg = cfg
        self.sampler = sampler
        self.scheduler = scheduler
        self.clip_skip = clip_skip
        self.model_files = list(model_files or [])
        self.negative_prompt = negative_prompt
        self.opener = opener or urllib.request.urlopen
        self.clock = clock
        self.sleeper = sleeper
        self.seed_factory = seed_factory
        self.turnaround_workflow_path = (
            Path(turnaround_workflow_path) if turnaround_workflow_path else None
        )
        self.turnaround_width = turnaround_width
        self.turnaround_height = turnaround_height
        self.turnaround_timeout_seconds = turnaround_timeout_seconds

    def _workflow_config(self, workflow_path: Optional[Path] = None) -> Dict[str, Any]:
        # Unregistered (custom) workflows are treated as SDXL and validated as such.
        return WORKFLOW_CONFIGS.get(
            (workflow_path or self.workflow_path).name,
            {"required_nodes": SDXL_REQUIRED_NODES, "injections": SDXL_INJECTIONS},
        )

    def _require_input(
        self, workflow: Dict[str, Any], target: Optional[Dict[str, str]], value: Any, role: str
    ) -> None:
        if not self._set_input(workflow, target, value):
            raise ComfyUIConfigurationError(
                f"Workflow {self.workflow_path.name} has no injection target for {role}"
            )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request_bytes(
        self,
        request: urllib.request.Request,
        timeout: Optional[float] = None,
    ) -> bytes:
        try:
            response = self.opener(request, timeout=timeout or self.timeout_seconds)
            try:
                return response.read()
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise ComfyUIConnectionError(
                f"Could not connect to ComfyUI at {self.base_url}: {exc}"
            ) from exc

    def _request_json(
        self,
        request: urllib.request.Request,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        raw = self._request_bytes(request, timeout=timeout)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyUIError("ComfyUI returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ComfyUIError("ComfyUI returned a JSON value instead of an object")
        return payload

    def check_connection(self) -> None:
        """画像生成開始前にlocalhostのComfyUIへ接続できることを確認する。"""
        request = urllib.request.Request(self._url("system_stats"), method="GET")
        self._request_json(request, timeout=min(self.timeout_seconds, 10.0))

    def free_memory(self) -> None:
        """ComfyUIにロード済みモデルの解放を依頼する。"""
        body = json.dumps(
            {"unload_models": True, "free_memory": True}
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url("free"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self._request_bytes(request, timeout=min(self.timeout_seconds, 10.0))

    def _load_workflow(self, workflow_path: Optional[Path] = None) -> Dict[str, Any]:
        selected_path = workflow_path or self.workflow_path
        try:
            with open(selected_path, "r", encoding="utf-8") as file:
                workflow = json.load(file)
        except OSError as exc:
            raise ComfyUIConfigurationError(
                f"Cannot read ComfyUI workflow: {selected_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ComfyUIConfigurationError(
                f"ComfyUI workflow is not valid JSON: {selected_path}"
            ) from exc

        if not isinstance(workflow, dict):
            raise ComfyUIConfigurationError("ComfyUI workflow must be a JSON object")
        for node_id, class_type in self._workflow_config(selected_path)["required_nodes"].items():
            node = workflow.get(node_id)
            if not isinstance(node, dict) or node.get("class_type") != class_type:
                raise ComfyUIConfigurationError(
                    f"Workflow node {node_id} must have class_type {class_type}"
                )
        return workflow

    @staticmethod
    def _set_input(
        workflow: Dict[str, Any], target: Optional[Dict[str, str]], value: Any
    ) -> bool:
        if not target:
            return False
        node = workflow.get(str(target.get("node")))
        if not isinstance(node, dict):
            return False
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            return False
        input_name = target.get("input")
        if not input_name:
            return False
        inputs[input_name] = value
        return True

    def _model_file_names(self) -> Dict[str, str]:
        names = {}
        for model_file in self.model_files:
            if isinstance(model_file, dict):
                subdir = model_file.get("subdir")
                filename = model_file.get("filename")
            else:
                subdir = getattr(model_file, "subdir", None)
                filename = getattr(model_file, "filename", None)
            if subdir and filename:
                names[str(subdir)] = str(filename)
        return names

    def _build_workflow(self, prompt: str, seed: int) -> Dict[str, Any]:
        return self._build_workflow_for(self.workflow_path, prompt, seed)

    def _build_workflow_for(
        self,
        workflow_path: Path,
        prompt: str,
        seed: int,
        reference_image: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Dict[str, Any]:
        workflow = copy.deepcopy(self._load_workflow(workflow_path))
        injections = self._workflow_config(workflow_path)["injections"]
        if self.model_files:
            model_file_names = self._model_file_names()
            targets = injections.get("model_files", {})
            for subdir, filename in model_file_names.items():
                self._require_input(
                    workflow, targets.get(subdir), filename, f"model file ({subdir})"
                )
        else:
            self._require_input(
                workflow, injections.get("model"), self.checkpoint_name, "model"
            )

        values = {
            "positive_prompt": prompt,
            "negative_prompt": self.negative_prompt,
            "seed": seed,
            "steps": self.steps,
            "cfg": self.cfg,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "width": self.width if width is None else width,
            "height": self.height if height is None else height,
            "batch_size": 1,
        }
        for role, value in values.items():
            if role in ("positive_prompt", "seed"):
                self._require_input(workflow, injections.get(role), value, role)
            else:
                self._set_input(workflow, injections.get(role), value)

        if self.clip_skip is not None:
            clip_target = injections.get("clip_skip")
            clip_node = workflow.get(str((clip_target or {}).get("node")))
            if not isinstance(clip_node, dict) or clip_node.get("class_type") != "CLIPSetLastLayer":
                raise ComfyUIConfigurationError(
                    "The selected image model profile requires a CLIPSetLastLayer node "
                    f"for clip skip in {self.workflow_path.name}."
                )
            self._set_input(workflow, clip_target, self.clip_skip)
            for connection in clip_target.get("connections", []):
                self._set_input(workflow, connection, [str(clip_target["node"]), 0])
        if reference_image is not None:
            self._require_input(
                workflow,
                injections.get("reference_image"),
                reference_image,
                "reference image",
            )
        return workflow

    def _queue(self, workflow: Dict[str, Any]) -> str:
        body = json.dumps({"prompt": workflow}).encode("utf-8")
        request = urllib.request.Request(
            self._url("prompt"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        payload = self._request_json(request)
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            details = payload.get("node_errors") or payload
            raise ComfyUIError(f"ComfyUI did not return prompt_id: {details}")
        return prompt_id

    def _wait_for_history(
        self, prompt_id: str, timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            request = urllib.request.Request(
                self._url(f"history/{urllib.parse.quote(prompt_id, safe='')}")
            )
            history = self._request_json(request)
            result = history.get(prompt_id)
            if isinstance(result, dict):
                status = result.get("status") or {}
                status_string = status.get("status_str")
                if status_string == "success":
                    return result
                if status_string == "error" or status.get("completed") is False:
                    details = status.get("messages") or result.get("node_errors")
                    raise ComfyUIError(
                        f"ComfyUI failed for prompt {prompt_id}: {details}"
                    )
            self.sleeper(self.poll_interval_seconds)
        raise ComfyUITimeoutError(
            f"ComfyUI timed out after {timeout}s for prompt {prompt_id}"
        )

    @staticmethod
    def _first_image(history: Dict[str, Any]) -> Dict[str, str]:
        outputs = history.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise ComfyUIError("ComfyUI history has no outputs object")
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            images = node_output.get("images") or []
            for image in images:
                if not isinstance(image, dict):
                    continue
                filename = image.get("filename")
                if filename:
                    return {
                        "filename": str(filename),
                        "subfolder": str(image.get("subfolder", "")),
                        "type": str(image.get("type", "output")),
                    }
        raise ComfyUIError("ComfyUI completed without returning an image")

    def _download_image(self, image: Dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(image)
        request = urllib.request.Request(self._url(f"view?{query}"))
        data = self._request_bytes(request)
        if not data:
            raise ComfyUIError("ComfyUI returned an empty image")
        return data

    def upload_image(self, image_path: Path) -> str:
        """Upload a local reference image and return ComfyUI's LoadImage name."""
        path = Path(image_path)
        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            raise ComfyUIError(f"Cannot read reference image: {path}") from exc

        boundary = f"----CodexComfyUI{uuid.uuid4().hex}"
        boundary_bytes = boundary.encode("ascii")
        filename = path.name.replace('"', "_")
        parts = [
            b"--" + boundary_bytes + b"\r\n"
            + f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode("utf-8")
            + b"Content-Type: image/png\r\n\r\n"
            + image_bytes
            + b"\r\n",
            b"--" + boundary_bytes + b"\r\n"
            + b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
            b"--" + boundary_bytes + b"--\r\n",
        ]
        request = urllib.request.Request(
            self._url("upload/image"),
            data=b"".join(parts),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(sum(len(part) for part in parts)),
            },
            method="POST",
        )
        payload = self._request_json(request)
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise ComfyUIError(f"ComfyUI upload did not return an image name: {payload}")
        subfolder = payload.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else name

    upload_reference_image = upload_image

    def _save_image_bytes(
        self, image_bytes: bytes, output_dir: Path, filename_stem: str
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"{filename_stem}.png"
        temporary = destination.with_suffix(".png.tmp")
        try:
            with open(temporary, "wb") as file:
                file.write(image_bytes)
            os.replace(temporary, destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ComfyUIError(f"Cannot save generated image: {destination}") from exc
        return destination

    def generate(
        self,
        prompt: str,
        output_dir: Path,
        filename_stem: str,
    ) -> GeneratedImage:
        seed = self.seed_factory()
        workflow = self._build_workflow(prompt, seed)
        prompt_id = self._queue(workflow)
        history = self._wait_for_history(prompt_id)
        image_info = self._first_image(history)
        image_bytes = self._download_image(image_info)
        destination = self._save_image_bytes(image_bytes, output_dir, filename_stem)
        return GeneratedImage(path=destination, seed=seed)

    def generate_turnaround(
        self,
        prompt: str,
        reference_image_path: Path,
        output_dir: Path,
        filename_stem: str = "turnaround",
    ) -> GeneratedImage:
        """Generate a turnaround sheet using an uploaded full-body reference."""
        if self.turnaround_workflow_path is None:
            raise ComfyUIConfigurationError("This image model has no turnaround workflow")
        uploaded_name = self.upload_image(Path(reference_image_path))
        seed = self.seed_factory()
        workflow = self._build_workflow_for(
            self.turnaround_workflow_path,
            prompt,
            seed,
            reference_image=uploaded_name,
            width=self.turnaround_width,
            height=self.turnaround_height,
        )
        prompt_id = self._queue(workflow)
        history = self._wait_for_history(
            prompt_id, timeout_seconds=self.turnaround_timeout_seconds
        )
        image_info = self._first_image(history)
        image_bytes = self._download_image(image_info)
        destination = self._save_image_bytes(image_bytes, output_dir, filename_stem)
        return GeneratedImage(path=destination, seed=seed)

    generate_with_reference = generate_turnaround
