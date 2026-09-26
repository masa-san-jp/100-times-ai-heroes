"""ローカル導入スクリプトの副作用を伴わないテスト。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import setup_local  # noqa: E402
from comfyui_config import resolve_comfyui_dir, resolve_comfyui_venv  # noqa: E402
from image_model_profiles import get_image_model_profile  # noqa: E402


def test_comfyui_venv_default_keeps_legacy_in_repo_path(tmp_path):
    comfyui_dir = resolve_comfyui_dir(tmp_path)

    assert resolve_comfyui_venv(tmp_path, comfyui_dir) == (
        tmp_path / ".runtime" / "comfyui-venv"
    ).resolve()


def test_comfyui_venv_default_is_sibling_for_external_comfyui(tmp_path):
    comfyui_dir = resolve_comfyui_dir(tmp_path, "~/ComfyUI")

    assert resolve_comfyui_venv(tmp_path, comfyui_dir) == (
        comfyui_dir.parent / "comfyui-venv"
    ).resolve()


def test_comfyui_venv_explicit_env_and_cli_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("COMFYUI_VENV", "relative/env-venv")
    env_args = setup_local.parse_args([])
    cli_args = setup_local.parse_args(["--comfyui-venv", "relative/cli-venv"])
    comfyui_dir = resolve_comfyui_dir(tmp_path, "external/ComfyUI")

    assert resolve_comfyui_venv(tmp_path, comfyui_dir, env_args.comfyui_venv) == (
        tmp_path / "relative" / "env-venv"
    ).resolve()
    assert resolve_comfyui_venv(tmp_path, comfyui_dir, cli_args.comfyui_venv) == (
        tmp_path / "relative" / "cli-venv"
    ).resolve()


def test_comfyui_paths_expand_user_and_repo_relative_values(tmp_path):
    comfyui_dir = resolve_comfyui_dir(tmp_path, "relative/ComfyUI")
    comfyui_venv = resolve_comfyui_venv(tmp_path, comfyui_dir, "relative/venv")

    assert comfyui_dir == (tmp_path / "relative" / "ComfyUI").resolve()
    assert comfyui_venv == (tmp_path / "relative" / "venv").resolve()


def test_download_metadata_exists_for_all_comparison_profiles():
    profile_ids = [
        "animagine-xl-4.0-opt",
        "illustrious-xl-v2",
        "pony-v6-xl",
        "noobai-xl-1.1",
    ]

    for profile_id in profile_ids:
        profile = get_image_model_profile(
            profile_id,
            PROJECT_ROOT / "config" / "comfyui" / "model_profiles.json",
        )
        assert profile.source_url.startswith("https://huggingface.co/")
        assert len(profile.model_sha256) == 64
        assert setup_local._model_download_url(profile).endswith(
            f"/{profile.checkpoint_name}?download=true"
        )


def test_download_model_skips_a_verified_existing_file(tmp_path, capsys):
    contents = b"verified model fixture"
    destination = tmp_path / "models" / "checkpoints" / "fixture.safetensors"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(contents)
    profile = SimpleNamespace(
        profile_id="fixture",
        checkpoint_name=destination.name,
        source_url="https://huggingface.co/example/model",
        model_sha256=hashlib.sha256(contents).hexdigest(),
    )

    setup_local._download_model(profile, destination, assume_yes=False, dry_run=False)

    assert "image model is installed" in capsys.readouterr().out
