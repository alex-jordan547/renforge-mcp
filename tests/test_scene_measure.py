import pytest

from renforge.scene_measure import measure_geometry


def box(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


def test_align_reports_edge_spreads_and_tolerance_pass() -> None:
    targets = [box(10, 20, 30, 10), box(12, 20, 28, 10)]

    result = measure_geometry("align", targets, tolerance=2)
    assert result["result"] == {
        "left": 2,
        "right": 0,
        "top": 0,
        "bottom": 0,
        "center_x": 1,
        "center_y": 0,
    }
    assert result["pass"] is True
    assert measure_geometry("align", targets, tolerance=1)["pass"] is False


def test_gap_orders_side_by_side_boxes() -> None:
    result = measure_geometry("gap", [box(50, 10, 20, 12), box(10, 10, 30, 12)])

    assert result["result"] == {"horizontal": 10, "vertical": -12, "overlaps": False}


def test_gap_zero_tolerance_passes_when_either_axis_separates_boxes() -> None:
    side_by_side = [box(50, 10, 20, 12), box(10, 10, 30, 12)]
    overlapping = [box(10, 20, 30, 20), box(25, 30, 30, 20)]

    assert measure_geometry("gap", side_by_side, tolerance=0)["pass"] is True
    assert measure_geometry("gap", overlapping, tolerance=0)["pass"] is False


def test_gap_orders_each_axis_independently() -> None:
    result = measure_geometry(
        "gap",
        [box(0, 100, 10, 10), box(5, 0, 10, 10)],
        tolerance=0,
    )

    assert result["result"] == {"horizontal": -5, "vertical": 90, "overlaps": False}
    assert result["pass"] is True


def test_gap_is_symmetric_when_boxes_share_an_origin() -> None:
    large = box(0, 0, 100, 100)
    small = box(0, 0, 10, 10)

    forward = measure_geometry("gap", [large, small])
    reverse = measure_geometry("gap", [small, large])

    assert forward["result"] == reverse["result"]
    assert forward["result"] == {"horizontal": -10, "vertical": -10, "overlaps": True}


def test_overlap_and_gap_detect_overlapping_boxes() -> None:
    first, second = box(10, 20, 30, 20), box(25, 30, 30, 20)

    overlap = measure_geometry("overlap", [first, second])
    assert overlap["result"] == {
        "area": 150,
        "ratio": 0.25,
        "rect": {"x": 25, "y": 30, "width": 15, "height": 10},
    }
    gap = measure_geometry("gap", [first, second])
    assert gap["result"] == {"horizontal": -15, "vertical": -10, "overlaps": True}


def test_overlap_tolerance_is_minimum_intersection_area() -> None:
    first, second = box(10, 20, 30, 20), box(25, 30, 30, 20)

    assert measure_geometry("overlap", [first, second], tolerance=150)["pass"] is True
    assert measure_geometry("overlap", [first, second], tolerance=151)["pass"] is False
    assert measure_geometry(
        "overlap", [first, box(100, 100, 10, 10)], tolerance=0
    )["pass"] is False


def test_distribute_reports_even_and_uneven_gaps() -> None:
    even = measure_geometry(
        "distribute", [box(0, 0, 10, 10), box(20, 0, 10, 10), box(40, 0, 10, 10)]
    )
    assert even["result"] == {"axis": "x", "gaps": [10, 10], "max_deviation": 0, "even": True}

    uneven = measure_geometry(
        "distribute", [box(0, 0, 10, 10), box(20, 0, 10, 10), box(45, 0, 10, 10)]
    )
    assert uneven["result"] == {"axis": "x", "gaps": [10, 15], "max_deviation": 5, "even": False}


def test_distribute_zero_tolerance_requires_exact_spacing() -> None:
    targets = [box(0, 0, 10, 10), box(20, 0, 10, 10), box(41, 0, 10, 10)]

    result = measure_geometry("distribute", targets, tolerance=0)

    assert result["result"]["max_deviation"] == 1
    assert result["result"]["even"] is False
    assert result["pass"] is False


def test_center_reports_offset_from_container_center() -> None:
    result = measure_geometry(
        "center", [box(10, 15, 20, 10)], within=box(0, 0, 100, 80), tolerance=30
    )

    assert result["result"] == {"dx": -30.0, "dy": -20.0}
    assert result["pass"] is True


def test_fit_reports_overflow_on_one_side() -> None:
    result = measure_geometry("fit", [box(10, 5, 100, 30)], within=box(0, 0, 100, 100))

    assert result["result"] == {
        "fits": False,
        "overflow": {"left": 0, "right": 10, "top": 0, "bottom": 0},
    }


def test_measure_geometry_rejects_wrong_target_counts() -> None:
    with pytest.raises(ValueError):
        measure_geometry("align", [box(0, 0, 1, 1)])
    with pytest.raises(ValueError):
        measure_geometry("gap", [box(0, 0, 1, 1)])
    with pytest.raises(ValueError):
        measure_geometry("distribute", [box(0, 0, 1, 1), box(2, 0, 1, 1)])
    with pytest.raises(ValueError):
        measure_geometry("overlap", [box(0, 0, 1, 1)])
    with pytest.raises(ValueError):
        measure_geometry("center", [box(0, 0, 1, 1), box(2, 0, 1, 1)])
    with pytest.raises(ValueError):
        measure_geometry("fit", [box(0, 0, 1, 1), box(2, 0, 1, 1)])
