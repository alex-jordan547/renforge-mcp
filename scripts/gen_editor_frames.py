#!/usr/bin/env python
"""Generate the editor's nine-patch panel frames.

Ren'Py has no rounded-corner primitive: ``Frame()`` divides an image into nine
parts and stretches the middle, so a rounded panel needs a real image. These are
generated rather than committed as opaque binaries, so the radii and colours
stay reviewable and stay tied to the design tokens.

    python scripts/gen_editor_frames.py

Corner radius is fixed in source pixels while the rest of the chrome scales with
the game's width, so a corner reads slightly rounder on a small window and
slightly tighter on a large one. Radius 24 was chosen to stay unobtrusive across
the 1280-to-2560 range real projects use.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "src" / "renforge" / "bridge" / "editor_assets" / "frames"

RADIUS = 24
# 4 px of straight edge between the corners keeps the stretchable middle
# non-degenerate, which Frame() needs to tile without artefacts.
SIZE = RADIUS * 2 + 4

# Straight from the maquette's :root — panel fill, header fill, hairline.
PANEL_FILL = (39, 39, 41, 240)
HEAD_FILL = (42, 42, 44, 245)
HAIRLINE = (255, 255, 255, 26)


def rounded(path: Path, fill: tuple[int, int, int, int]) -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, SIZE - 1, SIZE - 1),
        radius=RADIUS,
        fill=fill,
        outline=HAIRLINE,
        width=1,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    print(f"{path.relative_to(REPO_ROOT)}  {SIZE}x{SIZE}  radius {RADIUS}")


def main() -> int:
    rounded(OUT_DIR / "panel.png", PANEL_FILL)
    rounded(OUT_DIR / "panel_head.png", HEAD_FILL)
    print(f"\nFrame borders: {RADIUS + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
