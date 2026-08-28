#!/usr/bin/env python3
"""Set up the local Ollama + ComfyUI runtime for this project.

The script intentionally keeps third-party runtimes under ``.runtime`` and
downloads only the selected image model.  It does not expose ComfyUI to the
network and it verifies model downloads before installing them.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import venv
from pathlib import Path
from typing import Iterable, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
PROJECT_VENV = PROJECT_ROOT / ".venv"
DEFAULT_COMFYUI_DIR = RUNTIME_DIR / "ComfyUI"
DEFAULT_COMFYUI_VENV = RUNTIME_DIR / "comfyui-venv"
COMFYUI_REPOSITORY = "https://github.com/Comfy-Org/ComfyUI.git"
DEFAULT_LLM_MODEL = "gpt-oss:20b"
DEFAULT_IMAGE_PROFILE = "animagine-xl-4.0-opt"
MINIMUM_MODEL_FREE_BYTES = 12 * 1024**3


class SetupError(RuntimeError):
    """Raised when the local setup cannot safely continue."""


def _python_executable(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(command: Sequence[str], *, cwd: Optional[Path] = None, dry_run: bool = False) -> None:
    printable = " ".join(subprocess.list2cmdline([str(item)]) for item in command)
    print(f"$ {printable}")
    if dry_run:
        return
    try:
        subprocess.run([str(item) for item in command], cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise SetupError(f"コマンドが見つかりません: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SetupError(f"コマンドに失敗しました (exit {exc.returncode}): {command[0]}") from exc


def _confirm(message: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise SetupError(f"確認が必要です。再実行時に --yes を指定してください: {message}")
    answer = input(f"{message} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _ensure_python() -> None:
    if sys.version_info < (3, 10):
        raise SetupError(
            "画像生成を含むセットアップには Python 3.10 以上が必要です。"
        )


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _create_venv(path: Path, *, dry_run: bool) -> Path:
    executable = _python_executable(path)
    if executable.exists():
        print(f"OK: virtual environment exists: {path}")
        return executable
    print(f"Creating virtual environment: {path}")
    if not dry_run:
        _ensure_directory(path.parent)
        venv.EnvBuilder(with_pip=True, clear=False).create(path)
    return executable


def _install_project_dependencies(*, dry_run: bool) -> None:
    python = _create_venv(PROJECT_VENV, dry_run=dry_run)
    _run(
        [python, "-m", "pip", "install", "-r", PROJECT_ROOT / "requirements.txt"],
        cwd=PROJECT_ROOT,
        dry_run=dry_run,
    )


def _ollama_is_running() -> bool:
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _start_ollama(*, dry_run: bool) -> None:
    if _ollama_is_running():
        print("OK: Ollama server is running")
        return
    ollama = shutil.which("ollama")
    if not ollama:
        raise SetupError(
            "Ollamaが見つかりません。macOSなら `brew install ollama`、"
            "その他の環境は https://ollama.com/download から導入してください。"
        )
    if dry_run:
        print("Would start Ollama: ollama serve")
        return
    log_path = RUNTIME_DIR / "ollama.log"
    _ensure_directory(RUNTIME_DIR)
    log_file = log_path.open("a", encoding="utf-8")
    subprocess.Popen(
        [ollama, "serve"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _ollama_is_running():
            print("OK: Ollama server started")
            return
        time.sleep(1)
    raise SetupError(f"Ollamaを起動できませんでした。ログを確認してください: {log_path}")


def _ensure_ollama_command(*, dry_run: bool, assume_yes: bool) -> None:
    if shutil.which("ollama"):
        return
    if platform.system() == "Darwin" and shutil.which("brew"):
        if not _confirm(
            "Ollamaがありません。Homebrewでインストールしますか?",
            assume_yes=assume_yes or dry_run,
        ):
            raise SetupError("Ollamaが必要です。`brew install ollama` を実行してください。")
        _run(["brew", "install", "ollama"], dry_run=dry_run)
        if not dry_run and not shutil.which("ollama"):
            raise SetupError("Ollamaのインストール後もコマンドが見つかりません。")
        return
    raise SetupError(
        "Ollamaが見つかりません。macOSなら `brew install ollama`、"
        "その他の環境は https://ollama.com/download から導入してください。"
    )


def _ollama_model_names() -> Iterable[str]:
    ollama = shutil.which("ollama")
    if not ollama:
        return []
    try:
        result = subprocess.run(
            [ollama, "list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    names = []
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields:
            names.append(fields[0])
    return names


def _ensure_ollama_model(model: str, *, dry_run: bool, assume_yes: bool) -> None:
    if model in _ollama_model_names() or f"{model}:latest" in _ollama_model_names():
        print(f"OK: Ollama model is installed: {model}")
        return
    if not _confirm(
        f"Ollamaモデル {model} をダウンロードします。モデルサイズはOllama側で表示されます。続行しますか?",
        assume_yes=assume_yes or dry_run,
    ):
        raise SetupError(f"モデルが必要です。後で実行してください: ollama pull {model}")
    _run([shutil.which("ollama") or "ollama", "pull", model], dry_run=dry_run)


def _git_is_repository(path: Path) -> bool:
    return (path / ".git").exists()


def _ensure_comfyui_checkout(path: Path, *, dry_run: bool) -> None:
    if path.exists() and (path / "main.py").exists():
        print(f"OK: ComfyUI installation exists: {path}")
        return
    if path.exists() and not _git_is_repository(path):
        if any(path.iterdir()):
            raise SetupError(
                f"ComfyUIの導入先が空ではありません: {path}\n"
                "COMFYUI_DIRで別の空ディレクトリを指定してください。"
            )
    if _git_is_repository(path):
        print(f"OK: ComfyUI checkout exists: {path}")
        return
    if not dry_run:
        _ensure_directory(path.parent)
    _run(["git", "clone", "--depth", "1", COMFYUI_REPOSITORY, path], dry_run=dry_run)


def _ensure_comfyui_dependencies(path: Path, *, dry_run: bool) -> Path:
    python = _create_venv(DEFAULT_COMFYUI_VENV, dry_run=dry_run)
    _run([python, "-m", "pip", "install", "--upgrade", "pip"], dry_run=dry_run)

    # The official ComfyUI guidance recommends a current PyTorch nightly for
    # Apple Silicon.  Installing it before requirements.txt prevents pip from
    # replacing the MPS-capable build with a CPU-only fallback.
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        _run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--pre",
                "torch",
                "torchvision",
                "torchaudio",
                "--extra-index-url",
                "https://download.pytorch.org/whl/nightly/cpu",
            ],
            dry_run=dry_run,
        )
    _run([python, "-m", "pip", "install", "-r", path / "requirements.txt"], dry_run=dry_run)
    return python


def _load_profile(profile_id: str):
    sys.path.insert(0, str(PROJECT_ROOT))
    from image_model_profiles import get_image_model_profile

    return get_image_model_profile(
        profile_id,
        PROJECT_ROOT / "config" / "comfyui" / "model_profiles.json",
    )


def _model_download_url(profile) -> str:
    if not profile.source_url or not profile.model_sha256:
        raise SetupError(
            f"profile {profile.profile_id} には安全な自動取得情報がありません。"
            "モデルカードからcheckpointを手動で配置してください。"
        )
    base = profile.source_url.rstrip("/")
    filename = urllib.parse.quote(profile.checkpoint_name)
    return f"{base}/resolve/main/{filename}?download=true"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_model(profile, destination: Path, *, assume_yes: bool, dry_run: bool) -> None:
    if destination.exists():
        if destination.is_file() and _sha256(destination) == profile.model_sha256:
            print(f"OK: image model is installed: {destination.name}")
            return
        if not _confirm(
            f"既存のモデル {destination} はチェックサムが一致しません。置き換えますか?",
            assume_yes=assume_yes,
        ):
            raise SetupError(f"モデルのチェックサムが不一致です: {destination}")

    if not dry_run:
        _ensure_directory(destination.parent)
    usage_path = destination.parent if destination.parent.exists() else PROJECT_ROOT
    free_bytes = shutil.disk_usage(usage_path).free
    if free_bytes < MINIMUM_MODEL_FREE_BYTES:
        raise SetupError(
            f"モデル導入に必要な空き容量が不足しています。"
            f"最低 {MINIMUM_MODEL_FREE_BYTES / 1024**3:.0f}GB 必要です。"
            f"現在: {free_bytes / 1024**3:.1f}GB"
        )

    url = _model_download_url(profile)
    if not _confirm(
        f"画像モデル {profile.profile_id}（約7GB）をダウンロードします。"
        f"保存先: {destination}\n続行しますか?",
        assume_yes=assume_yes or dry_run,
    ):
        raise SetupError("画像モデルの導入を中止しました。")
    if dry_run:
        print(f"Would download: {url}")
        return

    _ensure_directory(destination.parent)
    partial = destination.with_name(destination.name + ".part")
    existing_bytes = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={existing_bytes}-"} if existing_bytes else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    print(f"Downloading {profile.profile_id} ...")
    try:
        response = urllib.request.urlopen(request, timeout=60.0)
        status = getattr(response, "status", 200)
        if existing_bytes and status != 206:
            response.close()
            partial.unlink(missing_ok=True)
            existing_bytes = 0
            request = urllib.request.Request(url, method="GET")
            response = urllib.request.urlopen(request, timeout=60.0)
        with response:
            digest = hashlib.sha256()
            if existing_bytes:
                with partial.open("rb") as existing:
                    for chunk in iter(lambda: existing.read(8 * 1024 * 1024), b""):
                        digest.update(chunk)
            mode = "ab" if existing_bytes else "wb"
            downloaded = existing_bytes
            next_report = downloaded + 512 * 1024 * 1024
            with partial.open(mode) as output:
                while True:
                    chunk = response.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if downloaded >= next_report:
                        print(f"  downloaded: {downloaded / 1024**3:.1f}GB")
                        next_report += 512 * 1024 * 1024
            actual_hash = digest.hexdigest()
    except (urllib.error.URLError, OSError) as exc:
        raise SetupError(f"モデルのダウンロードに失敗しました。再実行すると再開できます: {exc}") from exc

    if actual_hash != profile.model_sha256:
        raise SetupError(
            f"モデルのSHA256が一致しません。期待値={profile.model_sha256}, 実際={actual_hash}\n"
            f"不完全なファイルは保持しています: {partial}"
        )
    os.replace(partial, destination)
    print(f"OK: image model installed: {destination}")


def _update_env(profile_id: str, comfyui_dir: Path) -> None:
    env_path = PROJECT_ROOT / ".env"
    template_path = PROJECT_ROOT / ".env.example"
    if not env_path.exists():
        shutil.copyfile(template_path, env_path)
        print(f"Created: {env_path}")

    values = {
        "COMFYUI_MODEL_PROFILE": profile_id,
        "COMFYUI_CHECKPOINT_NAME": "",
        "COMFYUI_DIR": str(comfyui_dir),
    }
    lines = env_path.read_text(encoding="utf-8").splitlines()
    seen = set()
    updated = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in values:
            updated.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"Updated: {env_path}")


def _ensure_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    template_path = PROJECT_ROOT / ".env.example"
    if not env_path.exists():
        shutil.copyfile(template_path, env_path)
        print(f"Created: {env_path}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="100 Times AI Heroes のローカル実行環境をセットアップします。"
    )
    parser.add_argument(
        "--profile",
        default=os.getenv("COMFYUI_MODEL_PROFILE", DEFAULT_IMAGE_PROFILE),
        help=f"導入する画像モデルprofile（既定: {DEFAULT_IMAGE_PROFILE}）",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("OLLAMA_MODEL", DEFAULT_LLM_MODEL),
        help=f"導入するOllamaモデル（既定: {DEFAULT_LLM_MODEL}）",
    )
    parser.add_argument(
        "--comfyui-dir",
        type=Path,
        default=Path(os.getenv("COMFYUI_DIR", str(DEFAULT_COMFYUI_DIR))),
        help="ComfyUI本体の導入先",
    )
    parser.add_argument("--skip-ollama", action="store_true", help="Ollamaの確認とモデル導入を省略")
    parser.add_argument("--skip-images", action="store_true", help="ComfyUIと画像モデルの導入を省略")
    parser.add_argument("--yes", action="store_true", help="大容量ダウンロードなどの確認に自動承認する")
    parser.add_argument("--dry-run", action="store_true", help="変更せず、実行予定の処理だけ表示する")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        _ensure_python()
        print("100 Times AI Heroes - local setup")
        print(f"Project: {PROJECT_ROOT}")
        if args.dry_run:
            print("DRY RUN: files and external services will not be changed")

        _install_project_dependencies(dry_run=args.dry_run)

        if not args.skip_ollama:
            _ensure_ollama_command(dry_run=args.dry_run, assume_yes=args.yes)
            _start_ollama(dry_run=args.dry_run)
            _ensure_ollama_model(args.llm_model, dry_run=args.dry_run, assume_yes=args.yes)
        else:
            print("SKIP: Ollama")

        if args.skip_images:
            print("SKIP: ComfyUI and image model")
        else:
            profile = _load_profile(args.profile)
            comfyui_dir = args.comfyui_dir.expanduser().resolve()
            _ensure_comfyui_checkout(comfyui_dir, dry_run=args.dry_run)
            _ensure_comfyui_dependencies(comfyui_dir, dry_run=args.dry_run)
            destination = comfyui_dir / "models" / "checkpoints" / profile.checkpoint_name
            _download_model(
                profile,
                destination,
                assume_yes=args.yes,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                _update_env(args.profile, comfyui_dir)

        if args.skip_images and not args.dry_run:
            _ensure_env_file()

        print("\nSetup complete.")
        print("Run: ./run_local.sh --iterations 1 --generate-images")
        return 0
    except (OSError, SetupError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
