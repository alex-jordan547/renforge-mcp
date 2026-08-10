#!/usr/bin/env python
"""Generate the editor's nine-patch panel frames.

Ren'Py has no rounded-corner primitive: ``Frame()`` divides an image into nine
parts and stretches the middle, so a rounded panel needs a real image. These are
generated rather than committed as opaque binaries, so the radii and colours
stay reviewable and stay tied to the design tokens.

    python scripts/gen_editor_frames.py

Corner radius is fixed in source pixels while the rest of the chrome scales with
the game's width, so a corner reads slightly rounder on a small window and
slightly tighter on a large one.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "src" / "renforge" / "bridge" / "editor_assets" / "frames"

# Maquette radii at 2560 (k=1.5): r-sm≈12, r-md≈18, r-lg≈27. Pill is half-height.
# Straight from the maquette's :root — panel fill, header fill, hairline.
PANEL_FILL = (39, 39, 41, 255)
HEAD_FILL = (42, 42, 44, 255)
SUNKEN_FILL = (0, 0, 0, 87)
SEGMENT_FILL = (255, 255, 255, 33)
HAIRLINE = (255, 255, 255, 26)


def rounded(
    path: Path,
    fill: tuple[int, int, int, int],
    *,
    radius: int,
    corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
    bottom_rule: bool = False,
    outline: tuple[int, int, int, int] | None = HAIRLINE,
) -> int:
    # 4 px of straight edge between the corners keeps the stretchable middle
    # non-degenerate, which Frame() needs to tile without artefacts.
    size = radius * 2 + 4
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=radius,
        fill=fill,
        outline=outline,
        width=1 if outline is not None else 0,
        corners=corners,
    )
    if bottom_rule:
        draw.line((0, size - 1, size - 1, size - 1), fill=HAIRLINE, width=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    shape = "".join("R" if corner else "-" for corner in corners)
    print(f"{path.relative_to(REPO_ROOT)}  {size}x{size}  radius {radius}  corners {shape}")
    return radius + 1  # recommended Frame() border


def main() -> int:
    panel_border = rounded(OUT_DIR / "panel.png", PANEL_FILL, radius=27)
    # The header is not a rounded box of its own. In the maquette it is a plain
    # band whose top corners are clipped by the panel's own radius, closed by a
    # hairline. Rounding its bottom would carve a notch out of the panel body.
    head_border = rounded(
        OUT_DIR / "panel_head.png",
        HEAD_FILL,
        radius=27,
        corners=(True, True, False, False),
        bottom_rule=True,
    )
    chip_border = rounded(OUT_DIR / "chip.png", SUNKEN_FILL, radius=12)
    segment_border = rounded(
        OUT_DIR / "seg_on.png",
        SEGMENT_FILL,
        radius=12,
        outline=None,
    )
    tools_border = rounded(OUT_DIR / "tools.png", SUNKEN_FILL, radius=18)
    # Pill: radius large enough that Frame() yields capsule ends at ~56 px height.
    pill_border = rounded(OUT_DIR / "pill.png", PANEL_FILL, radius=28, outline=None)
    accent_pill_border = rounded(
        OUT_DIR / "pill_accent.png",
        (0, 113, 227, 255),  # #0071e3
        radius=28,
        outline=None,
    )
    brand_border = rounded(
        OUT_DIR / "brand.png",
        (0, 113, 227, 255),
        radius=12,
        outline=None,
    )
    # Apple-thin overlay scrollbar: a capsule thumb only (no track). 20×40 so
    # Frame(border=10) keeps circular caps when the thumb stretches.
    scroll_border = _capsule(
        OUT_DIR / "scroll_thumb.png",
        (255, 255, 255, 90),
        size=(20, 40),
    )
    _capsule(
        OUT_DIR / "scroll_thumb_hover.png",
        (255, 255, 255, 150),
        size=(20, 40),
    )
    print(
        "\nFrame borders: "
        f"panel {panel_border}, head ({head_border}, {head_border}, {head_border}, 2), "
        f"chip {chip_border}, segment {segment_border}, tools {tools_border}, "
        f"pill {pill_border}, pill_accent {accent_pill_border}, brand {brand_border}, "
        f"scroll_thumb {scroll_border}"
    )
    return 0


def _capsule(
    path: Path,
    fill: tuple[int, int, int, int],
    *,
    size: tuple[int, int],
) -> int:
    """Fully rounded pill used as a scrollbar thumb nine-patch."""
    width, height = size
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=width // 2,
        fill=fill,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    print(f"{path.relative_to(REPO_ROOT)}  {width}x{height}  capsule")
    return width // 2


if __name__ == "__main__":
    raise SystemExit(main())
