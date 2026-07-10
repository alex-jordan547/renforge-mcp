import io
from pathlib import Path

import pytest


def _png(width: int = 20, height: int = 10) -> bytes:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    image = image_module.new("RGB", (width, height), "red")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _encoded(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_crop_coordinates_require_dimensions() -> None:
    from renforge.image_ops import transform_png

    with pytest.raises(ValueError, match="coordinates require"):
        transform_png(_png(), crop_x=2)


def test_crop_must_stay_inside_image() -> None:
    from renforge.image_ops import transform_png

    with pytest.raises(ValueError, match="exceeds image bounds"):
        transform_png(_png(), crop_x=15, crop_width=10, crop_height=10)


def test_find_image_matches_reports_bounds_and_center() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import find_image_matches

    screenshot = image_module.new("RGB", (18, 12), "black")
    for x in range(5, 9):
        for y in range(3, 7):
            screenshot.putpixel((x, y), (240, 80, 20))
    template = screenshot.crop((5, 3, 9, 7))

    result = find_image_matches(_encoded(screenshot), _encoded(template))

    assert result["ok"] is True
    assert result["matches"]
    match = result["matches"][0]
    assert match["bounds"] == {"x": 5, "y": 3, "width": 4, "height": 4}
    assert match["center"] == {"x": 7.0, "y": 5.0}
    assert match["score"] == 1.0
    assert match["confidence"] == 1.0
    assert result["screenshot"] == {"width": 18, "height": 12}
    assert result["template"] == {"width": 4, "height": 4}


def test_find_image_matches_can_return_multiple_non_overlapping_occurrences() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import find_image_matches

    screenshot = image_module.new("RGB", (30, 12), "black")
    for origin_x in (2, 20):
        for x in range(origin_x, origin_x + 3):
            for y in range(4, 7):
                screenshot.putpixel((x, y), (20, 200, 80))
    template = screenshot.crop((2, 4, 5, 7))

    result = find_image_matches(
        _encoded(screenshot),
        _encoded(template),
        threshold=1.0,
        max_matches=4,
    )

    assert [match["bounds"]["x"] for match in result["matches"]] == [2, 20]
    assert all(match["score"] == 1.0 for match in result["matches"])
    limited = find_image_matches(
        _encoded(screenshot),
        _encoded(template),
        threshold=1.0,
        max_matches=1,
    )
    assert len(limited["matches"]) == 1
    assert limited["truncated"] is True


def test_find_image_matches_ignores_transparent_template_pixels() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import find_image_matches

    screenshot = image_module.new("RGB", (10, 8), "black")
    screenshot.paste((250, 250, 250), (4, 2, 7, 5))
    template = image_module.new("RGBA", (5, 5), (255, 0, 0, 0))
    template.paste((250, 250, 250, 255), (1, 1, 4, 4))

    result = find_image_matches(_encoded(screenshot), _encoded(template), threshold=1.0)

    assert result["matches"][0]["bounds"] == {
        "x": 3,
        "y": 1,
        "width": 5,
        "height": 5,
    }


def test_find_image_matches_respects_region_and_rejects_unbounded_scan() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import find_image_matches

    screenshot = image_module.new("RGB", (20, 12), "black")
    screenshot.paste((255, 255, 255), (12, 4, 16, 8))
    template = screenshot.crop((12, 4, 16, 8))

    result = find_image_matches(
        _encoded(screenshot),
        _encoded(template),
        region=(10, 2, 10, 8),
    )
    assert result["matches"][0]["bounds"]["x"] == 12
    assert result["region"] == {"x": 10, "y": 2, "width": 10, "height": 8}

    with pytest.raises(ValueError, match="search region has"):
        find_image_matches(
            _encoded(screenshot),
            _encoded(template),
            max_scan_positions=1,
        )


def test_find_image_matches_accepts_template_path(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import find_image_matches

    screenshot = image_module.new("RGB", (8, 8), "black")
    screenshot.paste((100, 120, 140), (1, 2, 4, 5))
    template_path = tmp_path / "button.png"
    screenshot.crop((1, 2, 4, 5)).save(template_path)

    result = find_image_matches(_encoded(screenshot), template_path)

    assert result["matches"][0]["bounds"]["x"] == 1


def test_find_image_matches_rejects_bad_inputs_and_empty_alpha() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import find_image_matches

    with pytest.raises(ValueError, match="screenshot is not a readable image"):
        find_image_matches(b"not-an-image", _png(2, 2))

    transparent = image_module.new("RGBA", (2, 2), (255, 255, 255, 0))
    with pytest.raises(ValueError, match="no visible pixels"):
        find_image_matches(_png(4, 4), _encoded(transparent))
