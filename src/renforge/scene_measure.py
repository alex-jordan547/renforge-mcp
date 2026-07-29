"""Pixel-relationship measurements for logical scene bounds."""

from __future__ import annotations

from typing import Any


def _edges(bounds: dict[str, Any]) -> tuple[Any, Any, Any, Any, float, float]:
    x, y = bounds["x"], bounds["y"]
    width, height = bounds["width"], bounds["height"]
    return x, x + width, y, y + height, x + width / 2, y + height / 2


def _check_count(action: str, targets: list[dict[str, Any]], *, exact: int | None = None, minimum: int | None = None) -> None:
    count = len(targets)
    if exact is not None and count != exact:
        raise ValueError(f"{action} requires exactly {exact} target(s)")
    if minimum is not None and count < minimum:
        raise ValueError(f"{action} requires at least {minimum} targets")


def _response(action: str, result: dict[str, Any], passed: bool | None, tolerance: float | None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "action": action,
        "space": "logical",
        "result": result,
    }
    if tolerance is not None:
        response["pass"] = passed
    return response


def _align(targets: list[dict[str, Any]], tolerance: float | None) -> dict[str, Any]:
    _check_count("align", targets, minimum=2)
    values = [_edges(target) for target in targets]
    spreads = {
        "left": max(item[0] for item in values) - min(item[0] for item in values),
        "right": max(item[1] for item in values) - min(item[1] for item in values),
        "top": max(item[2] for item in values) - min(item[2] for item in values),
        "bottom": max(item[3] for item in values) - min(item[3] for item in values),
        "center_x": max(item[4] for item in values) - min(item[4] for item in values),
        "center_y": max(item[5] for item in values) - min(item[5] for item in values),
    }
    passed = max(spreads.values()) <= tolerance if tolerance is not None else None
    return _response("align", spreads, passed, tolerance)


def _gap(targets: list[dict[str, Any]], tolerance: float | None) -> dict[str, Any]:
    _check_count("gap", targets, exact=2)
    ordered = sorted(targets, key=lambda target: (target["x"], target["y"]))
    first, second = (_edges(target) for target in ordered)
    horizontal = second[0] - first[1]
    vertical = second[2] - first[3]
    result = {"horizontal": horizontal, "vertical": vertical, "overlaps": horizontal < 0 and vertical < 0}
    # A gap passes when neither axis overlaps beyond the supplied tolerance.
    passed = horizontal >= -tolerance and vertical >= -tolerance if tolerance is not None else None
    return _response("gap", result, passed, tolerance)


def _distribute(targets: list[dict[str, Any]], tolerance: float | None) -> dict[str, Any]:
    _check_count("distribute", targets, minimum=3)
    values = [_edges(target) for target in targets]
    x_spread = max(item[4] for item in values) - min(item[4] for item in values)
    y_spread = max(item[5] for item in values) - min(item[5] for item in values)
    axis = "x" if x_spread >= y_spread else "y"
    index = 0 if axis == "x" else 2
    edge_index = 1 if axis == "x" else 3
    ordered = sorted(values, key=lambda item: item[index])
    gaps = [ordered[i + 1][index] - ordered[i][edge_index] for i in range(len(ordered) - 1)]
    max_deviation = max(gaps) - min(gaps)
    even = max_deviation <= (tolerance or 1)
    return _response(
        "distribute",
        {"axis": axis, "gaps": gaps, "max_deviation": max_deviation, "even": even},
        even,
        tolerance,
    )


def _center(targets: list[dict[str, Any]], within: dict[str, Any] | None, tolerance: float | None) -> dict[str, Any]:
    _check_count("center", targets, exact=1)
    if within is None:
        raise ValueError("center requires within bounds")
    target = _edges(targets[0])
    container = _edges(within)
    dx, dy = target[4] - container[4], target[5] - container[5]
    passed = max(abs(dx), abs(dy)) <= tolerance if tolerance is not None else None
    return _response("center", {"dx": dx, "dy": dy}, passed, tolerance)


def _overlap(targets: list[dict[str, Any]], tolerance: float | None) -> dict[str, Any]:
    _check_count("overlap", targets, exact=2)
    first, second = (_edges(target) for target in targets)
    left, right = max(first[0], second[0]), min(first[1], second[1])
    top, bottom = max(first[2], second[2]), min(first[3], second[3])
    width, height = max(0, right - left), max(0, bottom - top)
    area = int(width * height)
    areas = [(item[1] - item[0]) * (item[3] - item[2]) for item in (first, second)]
    ratio = round(area / min(areas), 3) if min(areas) > 0 else 0
    rect = {"x": left, "y": top, "width": width, "height": height} if area else None
    passed = area > 0 if tolerance is not None else None
    return _response("overlap", {"area": area, "ratio": ratio, "rect": rect}, passed, tolerance)


def _fit(targets: list[dict[str, Any]], within: dict[str, Any] | None, tolerance: float | None) -> dict[str, Any]:
    _check_count("fit", targets, exact=1)
    if within is None:
        raise ValueError("fit requires within bounds")
    target, container = _edges(targets[0]), _edges(within)
    overflow = {
        "left": max(0, container[0] - target[0]),
        "right": max(0, target[1] - container[1]),
        "top": max(0, container[2] - target[2]),
        "bottom": max(0, target[3] - container[3]),
    }
    fits = all(value == 0 for value in overflow.values())
    passed = all(value <= tolerance for value in overflow.values()) if tolerance is not None else None
    return _response("fit", {"fits": fits, "overflow": overflow}, passed, tolerance)


def measure_geometry(
    action: str,
    targets: list[dict[str, Any]],
    *,
    within: dict[str, Any] | None = None,
    tolerance: float | None = None,
) -> dict[str, Any]:
    """Measure spatial relationships among literal logical bounds."""
    measurements = {
        "align": _align,
        "gap": _gap,
        "distribute": _distribute,
        "overlap": _overlap,
    }
    if action in measurements:
        return measurements[action](targets, tolerance)
    if action == "center":
        return _center(targets, within, tolerance)
    if action == "fit":
        return _fit(targets, within, tolerance)
    raise ValueError(f"unsupported geometry action: {action}")
