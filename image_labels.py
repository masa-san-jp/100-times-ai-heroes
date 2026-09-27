"""画像の下にキャラクター名と身長を描くラベル帯。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional, Union

from PIL import Image, ImageDraw, ImageFont


PathLike = Union[str, Path]


_MACOS_FONTS = (
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.otf",
)
_LINUX_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)


def _font_candidates() -> Iterable[str]:
    configured = os.getenv("LABEL_FONT_PATH", "").strip()
    if configured:
        yield configured
    yield from _MACOS_FONTS
    yield from _LINUX_FONTS


def _load_font(size: int, candidates: Iterable[str]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _fit_font(
    text: str,
    max_size: int,
    min_size: int,
    available_width: int,
    candidates: Iterable[str],
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(max_size, max(min_size, 1) - 1, -1):
        font = _load_font(size, candidates)
        left, top, right, bottom = font.getbbox(text)
        if right - left <= available_width:
            return font
    return _load_font(max(min_size, 1), candidates)


def label_image(
    source_path: PathLike,
    destination_path: PathLike,
    name: str,
    height_cm: int,
    margin_ratio: float = 0.0,
) -> Path:
    """Copy *source_path* to a new image with a white label band below it.

    The source image is never resized or cropped. With ``margin_ratio`` a white
    margin of that fraction of the shorter side is added around it first.
    """

    source = Path(source_path)
    destination = Path(destination_path)
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGBA") if opened.mode not in {"RGB", "RGBA"} else opened.copy()
    except OSError as exc:
        raise ValueError(f"Cannot read image for labeling: {source}") from exc

    if margin_ratio > 0:
        margin = round(min(image.size) * margin_ratio)
        padded = Image.new(image.mode, (image.width + margin * 2, image.height + margin * 2), "white")
        padded.paste(image, (margin, margin))
        image.close()
        image = padded

    band_height = max(80, round(image.height * 0.10))
    output = Image.new(image.mode, (image.width, image.height + band_height), "white")
    output.paste(image, (0, 0))
    draw = ImageDraw.Draw(output)
    candidates = tuple(_font_candidates())
    left_margin = max(16, round(image.width * 0.025))
    usable_width = max(1, image.width - left_margin * 2)

    name_font = _fit_font(
        str(name),
        max_size=max(24, round(band_height * 0.42)),
        min_size=max(12, round(band_height * 0.22)),
        available_width=usable_width,
        candidates=candidates,
    )
    height_text = f"身長 {int(height_cm)}cm"
    height_font = _fit_font(
        height_text,
        max_size=max(16, round(band_height * 0.27)),
        min_size=max(10, round(band_height * 0.15)),
        available_width=usable_width,
        candidates=candidates,
    )

    name_box = draw.textbbox((0, 0), str(name), font=name_font)
    height_box = draw.textbbox((0, 0), height_text, font=height_font)
    name_height = name_box[3] - name_box[1]
    height_height = height_box[3] - height_box[1]
    gap = max(4, round(band_height * 0.08))
    content_height = name_height + gap + height_height
    y = image.height + max(4, (band_height - content_height) // 2) - name_box[1]
    draw.text((left_margin, y), str(name), fill="black", font=name_font)
    y += name_box[3] + gap - height_box[1]
    draw.text((left_margin, y), height_text, fill="black", font=height_font)

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.save(destination, format="PNG")
    except OSError as exc:
        raise ValueError(f"Cannot save labeled image: {destination}") from exc
    finally:
        output.close()
        image.close()
    return destination


def add_image_label(
    source_path: PathLike,
    destination_path: PathLike,
    name: str,
    height_cm: int,
) -> Path:
    """Compatibility-friendly alias for :func:`label_image`."""

    return label_image(source_path, destination_path, name, height_cm)


def content_touches_edges(
    image_path: PathLike,
    *,
    band_ratio: float = 0.01,
    threshold: int = 230,
    min_ratio: float = 0.002,
) -> bool:
    """Return True when non-background pixels reach the image border.

    The border band is ``band_ratio`` of the width (left/right) or height
    (top/bottom), so figures must keep a real margin, not just avoid being cut.
    A pixel darker than ``threshold`` (0-255 luminance) counts as content; tiny
    noise below ``min_ratio`` is ignored.
    """

    with Image.open(image_path) as opened:
        gray = opened.convert("L")
    width, height = gray.size
    side = max(4, round(width * band_ratio))
    edge = max(4, round(height * band_ratio))
    regions = [
        (0, 0, side, height),
        (width - side, 0, width, height),
        (0, 0, width, edge),
        (0, height - edge, width, height),
    ]
    for box in regions:
        histogram = gray.crop(box).histogram()
        total = sum(histogram)
        dark = sum(histogram[:threshold])
        if total and dark / total >= min_ratio:
            return True
    return False
