"""Shared ComfyUI path resolution for setup and runtime scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union


PathValue = Union[str, Path]
DEFAULT_COMFYUI_DIR = Path(".runtime") / "ComfyUI"
DEFAULT_COMFYUI_VENV = Path(".runtime") / "comfyui-venv"


def resolve_path(value: PathValue, project_root: Path) -> Path:
    """Expand a configured path and resolve relative paths from the repo root."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_comfyui_dir(
    project_root: Path, configured_dir: Optional[PathValue] = None
) -> Path:
    """Resolve COMFYUI_DIR, using the in-repo default when it is unset."""
    value = (
        DEFAULT_COMFYUI_DIR
        if configured_dir is None or not str(configured_dir).strip()
        else configured_dir
    )
    return resolve_path(value, project_root)


def resolve_comfyui_venv(
    project_root: Path,
    comfyui_dir: PathValue,
    configured_venv: Optional[PathValue] = None,
) -> Path:
    """Resolve COMFYUI_VENV using the shared backward-compatible default rule."""
    if configured_venv is not None and str(configured_venv).strip():
        return resolve_path(configured_venv, project_root)

    resolved_comfyui_dir = resolve_path(comfyui_dir, project_root)
    default_comfyui_dir = resolve_path(DEFAULT_COMFYUI_DIR, project_root)
    if resolved_comfyui_dir == default_comfyui_dir:
        return resolve_path(DEFAULT_COMFYUI_VENV, project_root)
    return (resolved_comfyui_dir.parent / "comfyui-venv").resolve()
