import io

import pytest


def _png(width: int = 20, height: int = 10) -> bytes:
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    image = image_module.new("RGB", (width, height), "red")
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
