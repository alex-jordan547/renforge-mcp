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


def test_annotate_png_overlays_guides_without_resizing() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import annotate_png

    base = image_module.new("RGB", (200, 120), "navy")
    annotated = annotate_png(
        _encoded(base), grid=50, rulers=True, crosshair=(100, 60)
    )

    result = image_module.open(io.BytesIO(annotated))
    assert result.size == (200, 120)
    # The overlay must actually change pixels along a gridline.
    assert result.convert("RGB").getpixel((50, 60)) != (0, 0, 128)


def test_annotate_png_rejects_out_of_bounds_crosshair() -> None:
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import annotate_png

    with pytest.raises(ValueError, match="inside the image"):
        annotate_png(_png(20, 10), crosshair=(50, 5))


def test_annotate_png_rejects_tiny_grid_spacing() -> None:
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import annotate_png

    with pytest.raises(ValueError, match="at least 5 pixels"):
        annotate_png(_png(20, 10), grid=2)


def test_estimate_translation_finds_shifted_sprite() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import estimate_translation

    before = image_module.new("L", (60, 40), 0)
    after = image_module.new("L", (60, 40), 0)
    before.paste(255, (10, 8, 20, 18))
    after.paste(255, (13, 10, 23, 20))

    result = estimate_translation(before, after, max_shift=5)

    assert result["available"] is True
    assert (result["dx"], result["dy"]) == (3, 2)
    assert result["support"] == 100


def test_estimate_translation_ignores_bright_transparent_padding() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import estimate_translation

    before = image_module.new("RGBA", (80, 60), (255, 255, 255, 0))
    after = image_module.new("RGBA", (80, 60), (255, 255, 255, 0))
    for x in range(10, 20):
        for y in range(8, 18):
            before.putpixel((x, y), (220, 40, 40, 255))
    for x in range(13, 23):
        for y in range(10, 20):
            after.putpixel((x, y), (220, 40, 40, 255))

    result = estimate_translation(before, after, max_shift=5)

    assert result["available"] is True
    assert (result["dx"], result["dy"]) == (3, 2)


def test_estimate_translation_crops_to_active_bbox_on_large_canvas() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import estimate_translation

    before = image_module.new("L", (400, 300), 0)
    after = image_module.new("L", (400, 300), 0)
    before.paste(255, (40, 30, 52, 42))
    after.paste(255, (45, 34, 57, 46))

    result = estimate_translation(before, after, max_shift=8)

    assert result["available"] is True
    assert (result["dx"], result["dy"]) == (5, 4)


def test_estimate_translation_rejects_excessive_work_budget() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge import image_ops
    from renforge.image_ops import estimate_translation

    before = image_module.new("L", (500, 500), 0)
    after = image_module.new("L", (500, 500), 0)
    before.paste(255, (10, 10, 30, 30))
    after.paste(255, (20, 20, 40, 40))

    original_budget = image_ops._MAX_ESTIMATE_PIXEL_CHECKS
    image_ops._MAX_ESTIMATE_PIXEL_CHECKS = 1_000
    try:
        with pytest.raises(ValueError, match="work budget"):
            estimate_translation(before, after, max_shift=64)
    finally:
        image_ops._MAX_ESTIMATE_PIXEL_CHECKS = original_budget


def test_diff_images_locates_the_changed_region() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import diff_images

    before = image_module.new("RGB", (200, 120), "black")
    before.paste("white", (10, 10, 30, 30))
    after = image_module.new("RGB", (200, 120), "black")
    after.paste("white", (50, 10, 70, 30))

    result = diff_images(_encoded(before), _encoded(after))

    assert result["changed"] is True
    assert result["bounds"] == {"x": 10, "y": 10, "width": 60, "height": 20}
    assert result["center"] == {"x": 40, "y": 20}
    assert result["changed_pixels"] == 800
    assert result["total_pixels"] == 200 * 120


def test_diff_images_reports_no_change_for_identical_frames() -> None:
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import diff_images

    frame = _png(40, 30)
    result = diff_images(frame, frame)

    assert result["changed"] is False
    assert result["bounds"] is None
    assert result["changed_pixels"] == 0


def test_diff_images_threshold_absorbs_small_jitter() -> None:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import diff_images

    before = image_module.new("RGB", (20, 20), (100, 100, 100))
    after = image_module.new("RGB", (20, 20), (104, 104, 104))

    assert diff_images(_encoded(before), _encoded(after), threshold=10)["changed"] is False
    assert diff_images(_encoded(before), _encoded(after), threshold=0)["changed"] is True


def test_diff_images_rejects_mismatched_sizes() -> None:
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from renforge.image_ops import diff_images

    with pytest.raises(ValueError, match="differ in size"):
        diff_images(_png(20, 10), _png(30, 10))
