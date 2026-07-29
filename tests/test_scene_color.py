import io

import pytest


image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")


def _png(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_contrast_ratio_matches_wcag_extremes() -> None:
    from renforge.scene_color import contrast_ratio

    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0, abs=0.1)
    assert contrast_ratio((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0)


def test_dominant_color_accepts_png_bytes_and_subregion() -> None:
    from renforge.scene_color import dominant_color

    image = image_module.new("RGBA", (6, 2), (255, 0, 0, 255))
    for x in range(2):
        for y in range(2):
            image.putpixel((x, y), (0, 0, 255, 255))

    assert dominant_color(_png(image)) == "#FF0000"
    assert dominant_color(image, {"x": 0, "y": 0, "width": 2, "height": 2}) == "#0000FF"


def test_region_color_reports_dominant_sample() -> None:
    from renforge.scene_color import region_color

    image = image_module.new("RGB", (2, 2), (18, 52, 86))

    assert region_color(image) == {"dominant": "#123456", "sampled": True}


def test_region_contrast_detects_black_and_white_groups() -> None:
    from renforge.scene_color import region_contrast

    image = image_module.new("RGB", (4, 2), (0, 0, 0))
    for x in range(2, 4):
        for y in range(2):
            image.putpixel((x, y), (255, 255, 255))

    result = region_contrast(image)

    assert result["ratio"] == pytest.approx(21.0, abs=0.1)
    assert result["aa"] is True
    assert result["aaa"] is True


def test_region_contrast_uniform_region_is_safe() -> None:
    from renforge.scene_color import region_contrast

    result = region_contrast(image_module.new("RGBA", (3, 3), (40, 80, 120, 255)))

    assert result["ratio"] == 1.0
    assert result["aa"] is False
    assert result["aaa"] is False


def test_pixel_sampling_is_bounded(monkeypatch) -> None:
    from renforge import scene_color

    monkeypatch.setattr(scene_color, "_MAX_SAMPLES", 16)
    square = image_module.new("RGBA", (100, 100), (20, 40, 60, 255))
    wide = image_module.new("RGBA", (1000, 1), (20, 40, 60, 255))

    assert len(scene_color._pixels(square, None)) <= 16
    assert len(scene_color._pixels(wide, None)) <= 16
    assert scene_color._to_rgba(wide) is wide
