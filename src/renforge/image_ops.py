"""Image inspection primitives used by MCP screenshots and local assets.

The module deliberately only depends on Pillow.  In particular, template
matching is implemented here instead of shelling out to ImageMagick/OpenCV so
that an MCP tool can safely inspect a screenshot in environments where those
programs are not installed.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, Sequence

from PIL import Image


_MAX_INPUT_BYTES = 100 * 1024 * 1024
_MAX_OUTPUT_PIXELS = 50_000_000
# Template matching is intentionally bounded.  A caller can provide a smaller
# region (which is preferable for an agent looking for a UI control) or opt in
# to a larger budget explicitly.  Refusing an unbounded scan is important for
# screenshots supplied by an untrusted MCP client.
# Four million anchor checks covers a typical 1920x1080 frame with a small
# (20x20) control template while still putting a hard ceiling on CPU work.
_MAX_SCAN_POSITIONS = 4_000_000
_MAX_SCORE_PIXELS = 50_000_000
_MAX_MATCHES = 100
_MIN_TEMPLATE_PIXELS = 1
_MAX_TEMPLATE_PIXELS = 4_000_000
_RAW_MATCH_MULTIPLIER = 20


def _read_image_source(source: bytes | bytearray | memoryview | str | Path | Image.Image, *, name: str) -> Image.Image:
    """Decode an image source into an owned RGBA Pillow image.

    ``source`` may be encoded bytes, a local path, or a Pillow image.  The
    latter is useful to callers that already decoded a screenshot and avoids a
    needless encode/decode round trip.  All returned images are detached from
    the input file/stream before this function returns.
    """

    if isinstance(source, Image.Image):
        image = source.copy()
    elif isinstance(source, (str, Path)):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise ValueError(f"{name} exceeds the 100 MB safety limit")
        try:
            with Image.open(path) as opened:
                opened.load()
                image = opened.copy()
        except Exception as exc:
            raise ValueError(f"{name} is not a readable image") from exc
    elif isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        if len(data) > _MAX_INPUT_BYTES:
            raise ValueError(f"{name} exceeds the 100 MB safety limit")
        try:
            with Image.open(io.BytesIO(data)) as opened:
                opened.load()
                image = opened.copy()
        except Exception as exc:
            raise ValueError(f"{name} is not a readable image") from exc
    else:
        raise TypeError(
            f"{name} must be encoded image bytes, a path, or a Pillow Image"
        )

    # Pillow protects against decompression bombs by default, but checking the
    # final dimensions here keeps this API deterministic if the global Pillow
    # warning policy is changed by an embedding application.
    if image.width <= 0 or image.height <= 0:
        raise ValueError(f"{name} has invalid dimensions")
    if image.width * image.height > _MAX_OUTPUT_PIXELS:
        raise ValueError(f"{name} exceeds the 50 megapixel safety limit")
    return image.convert("RGBA")


def _normalise_region(
    region: Sequence[int] | dict[str, int] | None,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Validate and clamp an optional ``(x, y, width, height)`` region."""

    if region is None:
        return 0, 0, width, height
    if isinstance(region, dict):
        try:
            values = (region["x"], region["y"], region["width"], region["height"])
        except (KeyError, TypeError) as exc:
            raise ValueError("region must contain x, y, width, and height") from exc
    else:
        if isinstance(region, (str, bytes, bytearray)) or len(region) != 4:
            raise ValueError("region must be (x, y, width, height)")
        values = tuple(region)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("region coordinates and dimensions must be integers")
    x, y, region_width, region_height = values
    if x < 0 or y < 0 or region_width <= 0 or region_height <= 0:
        raise ValueError("region coordinates must be non-negative and dimensions positive")
    if x + region_width > width or y + region_height > height:
        raise ValueError(f"region exceeds image bounds {width}x{height}")
    return x, y, region_width, region_height


def _visible_anchor_points(alpha: bytes, width: int, height: int) -> list[tuple[int, int]]:
    """Return a small, deterministic set of opaque template sample points."""

    # Do not materialise every visible coordinate: a 2000x2000 template can
    # have four million pixels and a list of tuples would itself consume a
    # considerable amount of memory.  A bounding box plus one fallback point
    # is sufficient for the small anchor set below.
    first_visible: tuple[int, int] | None = None
    left, top = width, height
    right = bottom = -1
    for index, value in enumerate(alpha):
        if value < 16:
            continue
        point = (index % width, index // width)
        if first_visible is None:
            first_visible = point
        left = min(left, point[0])
        right = max(right, point[0])
        top = min(top, point[1])
        bottom = max(bottom, point[1])
    if first_visible is None:
        return []

    wanted = [
        (left, top),
        (right, top),
        (left, bottom),
        (right, bottom),
        ((left + right) // 2, (top + bottom) // 2),
        ((left + right) // 2, top),
        ((left + right) // 2, bottom),
        (left, (top + bottom) // 2),
        (right, (top + bottom) // 2),
    ]

    # Transparent corners are common in UI assets.  Fall back to a visible
    # pixel when a wanted point is transparent, while preserving the requested
    # order to make candidate filtering deterministic.
    anchors: list[tuple[int, int]] = []
    for point in wanted:
        if alpha[point[1] * width + point[0]] < 16:
            point = first_visible
        if point not in anchors:
            anchors.append(point)
    return anchors


def _pixel_distance(
    screen: bytes,
    template: bytes,
    screen_index: int,
    template_index: int,
) -> float:
    """Return RGB distance for two RGBA byte offsets, normalised to 0..1."""

    return (
        abs(screen[screen_index] - template[template_index])
        + abs(screen[screen_index + 1] - template[template_index + 1])
        + abs(screen[screen_index + 2] - template[template_index + 2])
    ) / 765.0


def _score_at(
    screen: bytes,
    screen_width: int,
    template: bytes,
    template_alpha: bytes,
    template_width: int,
    template_height: int,
    x: int,
    y: int,
) -> tuple[float, int]:
    """Score one candidate and return ``(score, visible_pixel_count)``."""

    difference = 0.0
    weight_total = 0.0
    visible_count = 0
    for row in range(template_height):
        screen_offset = ((y + row) * screen_width + x) * 4
        template_offset = row * template_width * 4
        alpha_offset = row * template_width
        for column in range(template_width):
            alpha = template_alpha[alpha_offset + column]
            if alpha < 16:
                continue
            # Weight partially transparent pixels by opacity.  Fully
            # transparent padding therefore has no effect on confidence.
            weight = alpha / 255.0
            difference += _pixel_distance(
                screen,
                template,
                screen_offset + column * 4,
                template_offset + column * 4,
            ) * weight
            weight_total += weight
            visible_count += 1
    if weight_total <= 0:
        return 0.0, visible_count
    return max(0.0, 1.0 - difference / weight_total), visible_count


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection area divided by the smaller rectangle area."""

    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[0] + a[2], b[0] + b[2])
    bottom = min(a[1] + a[3], b[1] + b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    return intersection / min(a[2] * a[3], b[2] * b[3])


def find_image_matches(
    screenshot: bytes | bytearray | memoryview | str | Path | Image.Image,
    template: bytes | bytearray | memoryview | str | Path | Image.Image,
    *,
    threshold: float = 0.95,
    max_matches: int = 20,
    region: Sequence[int] | dict[str, int] | None = None,
    max_scan_positions: int = _MAX_SCAN_POSITIONS,
) -> dict[str, Any]:
    """Locate an image template in a screenshot using Pillow only.

    ``screenshot`` and ``template`` can be encoded image bytes, local paths, or
    already-open Pillow images.  Matching is exact-size RGB comparison with a
    normalised confidence score (``1.0`` is identical).  Transparent template
    pixels are ignored, which makes PNG UI assets with transparent padding
    useful as templates.

    The return value is JSON-ready::

        {
            "ok": True,
            "matches": [{
                "score": 1.0,
                "confidence": 1.0,
                "bounds": {"x": 120, "y": 30, "width": 64, "height": 32},
                "center": {"x": 152.0, "y": 46.0},
            }],
            "screenshot": {"width": 1280, "height": 720},
            "template": {"width": 64, "height": 32},
            "threshold": 0.95,
            "scanned": 1234,
            "truncated": False,
        }

    Candidate rectangles with more than 50% overlap are coalesced, avoiding a
    flood of near-identical results when an asset sits on a flat background.
    Raw candidates are also capped at twenty times ``max_matches`` so a
    low-information template cannot consume unbounded memory.
    ``ValueError`` is raised for invalid images or an over-budget search area;
    callers such as MCP tools can turn that into their normal ``ok: false``
    error response.
    """

    try:
        threshold = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold must be a number between 0 and 1") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if isinstance(max_matches, bool) or not isinstance(max_matches, int):
        raise ValueError("max_matches must be an integer")
    if not 1 <= max_matches <= _MAX_MATCHES:
        raise ValueError(f"max_matches must be between 1 and {_MAX_MATCHES}")
    if isinstance(max_scan_positions, bool) or not isinstance(max_scan_positions, int):
        raise ValueError("max_scan_positions must be an integer")
    if not 1 <= max_scan_positions <= 20_000_000:
        raise ValueError("max_scan_positions must be between 1 and 20000000")

    screen_image = _read_image_source(screenshot, name="screenshot")
    template_image = _read_image_source(template, name="template")
    screen_width, screen_height = screen_image.size
    template_width, template_height = template_image.size
    if template_width * template_height < _MIN_TEMPLATE_PIXELS:
        raise ValueError("template is empty")
    if template_width * template_height > _MAX_TEMPLATE_PIXELS:
        raise ValueError("template exceeds the 4 megapixel safety limit")
    if template_width > screen_width or template_height > screen_height:
        return {
            "ok": True,
            "matches": [],
            "screenshot": {"width": screen_width, "height": screen_height},
            "template": {"width": template_width, "height": template_height},
            "region": None,
            "threshold": threshold,
            "scanned": 0,
            "candidate_count": 0,
            "truncated": False,
        }

    region_x, region_y, region_width, region_height = _normalise_region(
        region,
        width=screen_width,
        height=screen_height,
    )
    if template_width > region_width or template_height > region_height:
        return {
            "ok": True,
            "matches": [],
            "screenshot": {"width": screen_width, "height": screen_height},
            "template": {"width": template_width, "height": template_height},
            "region": {
                "x": region_x,
                "y": region_y,
                "width": region_width,
                "height": region_height,
            },
            "threshold": threshold,
            "scanned": 0,
            "candidate_count": 0,
            "truncated": False,
        }

    positions_x = region_width - template_width + 1
    positions_y = region_height - template_height + 1
    candidate_count = positions_x * positions_y
    if candidate_count > max_scan_positions:
        raise ValueError(
            f"search region has {candidate_count} positions; limit is "
            f"{max_scan_positions}; provide a smaller region or increase max_scan_positions"
        )

    screen_rgba = screen_image.tobytes()
    template_rgba = template_image.tobytes()
    template_alpha = template_image.getchannel("A").tobytes()
    anchors = _visible_anchor_points(template_alpha, template_width, template_height)
    if not anchors:
        raise ValueError("template has no visible pixels")

    # Anchors are only a fast rejection filter.  The full score below decides
    # whether a candidate reaches the requested threshold.  This tolerance is
    # intentionally looser than the requested score to avoid dropping an
    # anti-aliased match before it can be scored.
    anchor_tolerance = max(0.15, (1.0 - threshold) * 2.5)
    matches: list[tuple[float, tuple[int, int, int, int]]] = []
    # Keep a bounded candidate list even for a low-information template (for
    # example a 1x1 solid-color pixel that matches a large flat background).
    # ``truncated`` tells the caller that it may want a narrower region or a
    # more distinctive template.
    raw_match_limit = max_matches * _RAW_MATCH_MULTIPLIER
    scanned = 0
    scored_pixels = 0
    truncated = False

    for offset_y in range(positions_y):
        y = region_y + offset_y
        for offset_x in range(positions_x):
            x = region_x + offset_x
            scanned += 1
            rejected = False
            for template_x, template_y in anchors:
                screen_index = ((y + template_y) * screen_width + x + template_x) * 4
                template_index = (template_y * template_width + template_x) * 4
                if _pixel_distance(screen_rgba, template_rgba, screen_index, template_index) > anchor_tolerance:
                    rejected = True
                    break
            if rejected:
                continue
            score, visible_count = _score_at(
                screen_rgba,
                screen_width,
                template_rgba,
                template_alpha,
                template_width,
                template_height,
                x,
                y,
            )
            if scored_pixels + visible_count > _MAX_SCORE_PIXELS:
                truncated = True
                break
            scored_pixels += visible_count
            if score >= threshold:
                if len(matches) < raw_match_limit:
                    matches.append((score, (x, y, template_width, template_height)))
                else:
                    truncated = True
        if truncated:
            break

    # Prefer the strongest match and suppress overlapping windows from a single
    # object.  Stable y/x tie-breakers make responses deterministic for agents.
    matches.sort(key=lambda item: (-item[0], item[1][1], item[1][0]))
    selected: list[tuple[float, tuple[int, int, int, int]]] = []
    for score, bounds in matches:
        if any(_overlap_ratio(bounds, previous_bounds) > 0.5 for _, previous_bounds in selected):
            continue
        selected.append((score, bounds))
        if len(selected) >= max_matches:
            break

    # ``truncated`` also tells an agent when the requested result limit hid
    # additional candidates.  This is useful when deciding whether to retry
    # with a larger ``max_matches`` or a narrower search region.
    output_truncated = truncated or len(matches) > len(selected)

    match_payload: list[dict[str, Any]] = []
    for score, (x, y, width, height) in selected:
        match_payload.append(
            {
                "score": round(float(score), 6),
                # ``confidence`` is the semantic name used by the proposed
                # MCP tool; keep ``score`` as a concise, implementation-neutral
                # alias for clients that already use template-match vocabulary.
                "confidence": round(float(score), 6),
                "bounds": {"x": x, "y": y, "width": width, "height": height},
                "center": {"x": x + width / 2.0, "y": y + height / 2.0},
            }
        )

    result: dict[str, Any] = {
        "ok": True,
        "matches": match_payload,
        "screenshot": {"width": screen_width, "height": screen_height},
        "template": {"width": template_width, "height": template_height},
        "region": {
            "x": region_x,
            "y": region_y,
            "width": region_width,
            "height": region_height,
        },
        "threshold": threshold,
        "scanned": scanned,
        "candidate_count": candidate_count,
        "truncated": output_truncated,
    }
    return result


def find_image_on_screen(
    screenshot: bytes | bytearray | memoryview | str | Path | Image.Image,
    template: bytes | bytearray | memoryview | str | Path | Image.Image,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility spelling for :func:`find_image_matches`.

    The MCP-facing tool name is likely to be ``find_image_on_screen``; keeping
    this alias at the image layer lets callers use that vocabulary without
    making the implementation depend on the server module.
    """

    return find_image_matches(screenshot, template, **kwargs)


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


__all__ = [
    "find_image_matches",
    "find_image_on_screen",
    "inspect_image",
    "transform_png",
]
