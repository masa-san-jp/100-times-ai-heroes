"""ローカル導入スクリプトの副作用を伴わないテスト。"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import run_local  # noqa: E402
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


def test_turbo_profile_custom_node_dry_run_prints_url_and_destination(tmp_path, capsys):
    profile = get_image_model_profile(
        "qwen-image-2.1-turbo",
        PROJECT_ROOT / "config" / "comfyui" / "model_profiles.json",
    )

    setup_local._download_model_files(profile, tmp_path, assume_yes=False, dry_run=True)

    output = capsys.readouterr().out
    custom_node = profile.custom_nodes[0]
    assert custom_node["url"] in output
    assert str(tmp_path / "custom_nodes" / "viggle_turbo.py") in output


def test_custom_node_hash_mismatch_discards_temp_file(monkeypatch, tmp_path):
    payload = b"not the pinned extension"

    class FakeResponse(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(
        setup_local.urllib.request,
        "urlopen",
        lambda _request, timeout=None: FakeResponse(payload),
    )
    destination = tmp_path / "custom_nodes" / "viggle_turbo.py"

    try:
        setup_local._download_custom_node_file(
            "qwen-image-2.1-turbo",
            "https://example.invalid/viggle_turbo.py",
            hashlib.sha256(b"expected extension").hexdigest(),
            destination,
            assume_yes=True,
            dry_run=False,
        )
    except setup_local.SetupError as exc:
        assert "SHA256" in str(exc)
    else:
        raise AssertionError("expected a SHA256 mismatch")

    assert not destination.exists()
    assert list(destination.parent.glob(".*.tmp")) == []


def test_turbo_is_the_setup_default(monkeypatch):
    monkeypatch.delenv("COMFYUI_MODEL_PROFILE", raising=False)
    assert setup_local.parse_args([]).profile == "qwen-image-2.1-turbo"


def test_comfyui_clone_is_pinned_to_release(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(setup_local, "_run", lambda command, dry_run: commands.append(command))

    setup_local._ensure_comfyui_checkout(tmp_path / "ComfyUI", dry_run=True)

    clone = commands[0]
    assert clone[clone.index("--branch") + 1] == setup_local.COMFYUI_REF
    assert setup_local.COMFYUI_REF.startswith("v")


def test_existing_comfyui_off_pinned_release_warns(monkeypatch, tmp_path, capsys):
    comfy = tmp_path / "ComfyUI"
    (comfy / ".git").mkdir(parents=True)
    (comfy / "main.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_local, "_git_describe", lambda path: "v0.0.1")

    setup_local._ensure_comfyui_checkout(comfy, dry_run=True)

    assert "WARNING" in capsys.readouterr().out


def test_run_local_passes_generator_options_through():
    args = run_local.parse_args(["--iterations", "1", "--generate-images", "--no-comfyui"])

    assert args.no_comfyui is True
    assert args.generator_args == ["--iterations", "1", "--generate-images"]


def test_run_local_accepts_double_dash_separator():
    args = run_local.parse_args(["--", "--iterations", "2"])

    assert args.generator_args[-2:] == ["--iterations", "2"]
