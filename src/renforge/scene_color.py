"""Colour and contrast primitives for structured scene perception."""

from __future__ import annotations

import io
from collections import Counter
from typing import Iterable

from PIL import Image


_ALPHA_THRESHOLD = 16


def _to_rgba(image: bytes | Image.Image) -> Image.Image:
    if isinstance(image, bytes):
        with Image.open(io.BytesIO(image)) as opened:
            return opened.convert("RGBA")
    if isinstance(image, Image.Image):
        return image.convert("RGBA")
    raise TypeError("image must be PNG bytes or a Pillow Image")


def _crop(image: Image.Image, box: dict | None) -> Image.Image:
    if box is None:
        return image
    x = box["x"]
    y = box["y"]
    return image.crop((x, y, x + box["width"], y + box["height"]))


def _pixels(image: bytes | Image.Image, box: dict | None) -> list[tuple[int, int, int, int]]:
    cropped = _crop(_to_rgba(image), box)
    return [
        cropped.getpixel((x, y))
        for y in range(cropped.height)
        for x in range(cropped.width)
    ]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    luminance1 = _relative_luminance(rgb1)
    luminance2 = _relative_luminance(rgb2)
    lighter = max(luminance1, luminance2)
    darker = min(luminance1, luminance2)
    return (lighter + 0.05) / (darker + 0.05)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def _average_color(pixels: Iterable[tuple[int, int, int, int]]) -> tuple[int, int, int]:
    values = list(pixels)
    if not values:
        return (0, 0, 0)
    count = len(values)
    return (
        round(sum(pixel[0] for pixel in values) / count),
        round(sum(pixel[1] for pixel in values) / count),
        round(sum(pixel[2] for pixel in values) / count),
    )


def dominant_color(image: bytes | Image.Image, box: dict | None = None) -> str:
    pixels = _pixels(image, box)
    visible = [pixel[:3] for pixel in pixels if pixel[3] >= _ALPHA_THRESHOLD]
    colors = Counter(visible or [pixel[:3] for pixel in pixels])
    if not colors:
        return "#000000"
    return _hex(colors.most_common(1)[0][0])


def region_color(image: bytes | Image.Image, box: dict | None = None) -> dict:
    return {"dominant": dominant_color(image, box), "sampled": True}


def region_contrast(image: bytes | Image.Image, box: dict | None = None) -> dict:
    pixels = _pixels(image, box)
    opaque = [pixel for pixel in pixels if pixel[3] >= _ALPHA_THRESHOLD]
    if not opaque:
        average = _average_color(pixels)
        ratio = 1.0
        return {
            "ratio": ratio,
            "fg": _hex(average),
            "bg": _hex(average),
            "aa": False,
            "aaa": False,
        }

    ordered = sorted(
        ((_relative_luminance(pixel[:3]), pixel) for pixel in opaque),
        key=lambda item: item[0],
    )
    boundaries = [
        index
        for index in range(1, len(ordered))
        if ordered[index - 1][0] < ordered[index][0]
    ]
    if boundaries:
        boundary = min(boundaries, key=lambda index: abs(index - len(ordered) / 2))
        dark = [pixel for _, pixel in ordered[:boundary]]
        light = [pixel for _, pixel in ordered[boundary:]]
    else:
        midpoint = len(ordered) // 2
        dark = [pixel for _, pixel in ordered[:midpoint]]
        light = [pixel for _, pixel in ordered[midpoint:]]

    if not dark or not light:
        average = _average_color(opaque)
        ratio = 1.0
        fg = bg = average
    elif len(dark) <= len(light):
        fg = _average_color(dark)
        bg = _average_color(light)
        ratio = contrast_ratio(fg, bg)
    else:
        fg = _average_color(light)
        bg = _average_color(dark)
        ratio = contrast_ratio(fg, bg)

    return {
        "ratio": round(ratio, 2),
        "fg": _hex(fg),
        "bg": _hex(bg),
        "aa": ratio >= 4.5,
        "aaa": ratio >= 7.0,
    }
