"""ローカル導入スクリプトの副作用を伴わないテスト。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import setup_local  # noqa: E402
from image_model_profiles import get_image_model_profile  # noqa: E402


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
