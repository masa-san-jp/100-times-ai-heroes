#!/usr/bin/env python3
"""Start local services and run the character generator."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()
PROJECT_PYTHON = PROJECT_ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
COMFYUI_PYTHON = RUNTIME_DIR / ("comfyui-venv/Scripts/python.exe" if os.name == "nt" else "comfyui-venv/bin/python")
COMFYUI_DIR = Path(os.environ.get("COMFYUI_DIR", str(RUNTIME_DIR / "ComfyUI"))).expanduser()
if not COMFYUI_DIR.is_absolute():
    COMFYUI_DIR = PROJECT_ROOT / COMFYUI_DIR
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def _is_available(url: str, endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/{endpoint.lstrip('/')}", timeout=2.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_for(url: str, endpoint: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_available(url, endpoint):
            return
        time.sleep(1)
    raise RuntimeError(f"サービスが起動しませんでした: {url}")


def _start_ollama() -> Optional[subprocess.Popen]:
    if _is_available(OLLAMA_URL, "/api/tags"):
        print("Using existing Ollama server")
        return None
    ollama = os.environ.get("OLLAMA_COMMAND", "ollama")
    log_path = RUNTIME_DIR / "ollama.log"
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [ollama, "serve"],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _wait_for(OLLAMA_URL, "/api/tags", timeout=30)
    print("Started Ollama server")
    return process


def _start_comfyui() -> Optional[subprocess.Popen]:
    if _is_available(COMFYUI_URL, "/system_stats"):
        print("Using existing ComfyUI server")
        return None
    if not COMFYUI_PYTHON.exists() or not COMFYUI_DIR.exists():
        raise RuntimeError("ComfyUIが未導入です。先に `./setup_local.sh` を実行してください。")
    log_path = RUNTIME_DIR / "comfyui.log"
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    process = subprocess.Popen(
        [
            COMFYUI_PYTHON,
            "main.py",
            "--listen",
            "127.0.0.1",
            "--port",
            "8188",
            "--disable-api-nodes",
            "--preview-method",
            "none",
        ],
        cwd=COMFYUI_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _wait_for(COMFYUI_URL, "/system_stats", timeout=120)
    print("Started ComfyUI server")
    return process


def _provider_is_openai(generator_args: Sequence[str]) -> bool:
    if os.environ.get("LLM_PROVIDER", "ollama").lower() == "openai":
        return True
    if "--provider=openai" in generator_args:
        return True
    try:
        index = generator_args.index("--provider")
    except ValueError:
        return False
    return generator_args[index + 1 : index + 2] == ["openai"]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ollama/ComfyUIを起動して、このプロジェクトを実行します。",
        add_help=True,
    )
    parser.add_argument(
        "--no-comfyui",
        action="store_true",
        help="既存のComfyUIを使わず、画像生成なしで実行する",
    )
    parser.add_argument(
        "--no-ollama",
        action="store_true",
        help="Ollamaを起動せず、指定されたproviderで実行する",
    )
    parser.add_argument("generator_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        generator_args = list(args.generator_args)
        if generator_args[:1] == ["--"]:
            generator_args = generator_args[1:]
        wants_images = "--generate-images" in generator_args or os.environ.get(
            "GENERATE_IMAGES", "false"
        ).lower() in {"1", "true", "yes", "on"}
        wants_openai = _provider_is_openai(generator_args)
        if not args.no_ollama and not wants_openai:
            _start_ollama()
        if wants_images:
            if args.no_comfyui:
                raise RuntimeError("--no-comfyui と --generate-images は同時に指定できません。")
            _start_comfyui()
        python = PROJECT_PYTHON if PROJECT_PYTHON.exists() else Path(sys.executable)
        command = [python, PROJECT_ROOT / "ollama_hero_gen.py", *generator_args]
        print("$ " + " ".join(str(item) for item in command))
        return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
