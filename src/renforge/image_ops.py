"""Image inspection primitives used by MCP screenshots and local assets."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


_MAX_INPUT_BYTES = 100 * 1024 * 1024
_MAX_OUTPUT_PIXELS = 50_000_000


def transform_png(
    data: bytes,
    *,
    crop_x: int = 0,
    crop_y: int = 0,
    crop_width: int = 0,
    crop_height: int = 0,
    scale: float = 1.0,
) -> bytes:
    """Crop and scale encoded image bytes, returning a normalized PNG."""

    if (crop_width == 0) != (crop_height == 0):
        raise ValueError("crop_width and crop_height must be provided together")
    if (crop_x or crop_y) and not (crop_width and crop_height):
        raise ValueError("crop coordinates require crop_width and crop_height")
    if crop_x < 0 or crop_y < 0 or crop_width < 0 or crop_height < 0:
        raise ValueError("crop coordinates and dimensions must be non-negative")
    if not 0.1 <= float(scale) <= 16.0:
        raise ValueError("scale must be between 0.1 and 16")

    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > _MAX_OUTPUT_PIXELS:
            raise ValueError("image exceeds the 50 megapixel safety limit")
        source.load()
        image = source.convert("RGBA" if "A" in source.getbands() else "RGB")

    if crop_width and crop_height:
        right = crop_x + crop_width
        bottom = crop_y + crop_height
        if right > image.width or bottom > image.height:
            raise ValueError(
                f"crop rectangle exceeds image bounds {image.width}x{image.height}"
            )
        image = image.crop((crop_x, crop_y, right, bottom))

    if scale != 1.0:
        width = max(1, round(image.width * float(scale)))
        height = max(1, round(image.height * float(scale)))
        if width * height > _MAX_OUTPUT_PIXELS:
            raise ValueError("scaled image exceeds the 50 megapixel safety limit")
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def inspect_image(
    image_path: str | Path,
    *,
    crop_x: int = 0,
    crop_y: int = 0,
    crop_width: int = 0,
    crop_height: int = 0,
    scale: float = 1.0,
) -> bytes:
    """Read, crop, and scale a local image without external scripts."""

    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("image exceeds the 100 MB safety limit")
    return transform_png(
        path.read_bytes(),
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_width,
        crop_height=crop_height,
        scale=scale,
    )


__all__ = ["inspect_image", "transform_png"]
