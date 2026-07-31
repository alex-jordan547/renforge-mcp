"""Render a scene snapshot as a compact ASCII wireframe."""

from __future__ import annotations

from typing import Any


_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _dimension(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mapped_rectangle(
    bounds: dict[str, Any], window_width: float, window_height: float, cols: int, rows: int
) -> tuple[int, int, int, int] | None:
    try:
        x = float(bounds["x"])
        y = float(bounds["y"])
        box_width = float(bounds["width"])
        box_height = float(bounds["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if window_width <= 0 or window_height <= 0:
        return None

    left = round(x * cols / window_width)
    top = round(y * rows / window_height)
    right = round((x + box_width) * cols / window_width)
    bottom = round((y + box_height) * rows / window_height)
    left, right = sorted((_clamp(left, 0, cols - 1), _clamp(right, 0, cols - 1)))
    top, bottom = sorted((_clamp(top, 0, rows - 1), _clamp(bottom, 0, rows - 1)))
    return left, top, right, bottom


def _text_for_legend(value: Any) -> str:
    text = " ".join(str(value).split())
    if len(text) > 30:
        text = text[:27] + "..."
    return text


def render_wireframe(nodes: list[dict], window: dict, *, width: int = 80) -> str:
    """Return a deterministic ASCII map and legend for *nodes*.

    Nodes are painted from low to high ``zorder``.  Nodes without measurable
    bounds are omitted from the map and reported after the legend.
    """
    cols = _clamp(_dimension(width, 80), 20, 200)
    window_width = _dimension(window.get("width", 0) if isinstance(window, dict) else 0, 0)
    window_height = _dimension(window.get("height", 0) if isinstance(window, dict) else 0, 0)
    if window_width > 0:
        row_count = round(cols * window_height / window_width * 0.5)
        rows = _clamp(row_count, 6, 60)
    else:
        rows = 6

    canvas = [[" " for _ in range(cols)] for _ in range(rows)]
    owners: list[list[int | None]] = [[None for _ in range(cols)] for _ in range(rows)]

    ordered = sorted(
        enumerate(nodes or []),
        key=lambda item: (_dimension(item[1].get("zorder", 0), 0), str(item[1].get("id", ""))),
    )
    drawable: list[tuple[int, dict, tuple[int, int, int, int]]] = []
    no_bounds: list[dict] = []
    for _, node in ordered:
        bounds = node.get("bounds") if isinstance(node, dict) else None
        if not isinstance(node, dict) or not node.get("bounds_available") or not isinstance(bounds, dict):
            if isinstance(node, dict):
                no_bounds.append(node)
            continue
        rectangle = _mapped_rectangle(bounds, window_width, window_height, cols, rows)
        if rectangle is None:
            no_bounds.append(node)
        else:
            drawable.append((len(drawable), node, rectangle))

    legend: list[str] = []
    labels_truncated = False
    for draw_index, node, (left, top, right, bottom) in drawable:
        if draw_index < len(_LABELS):
            label = _LABELS[draw_index]
        else:
            label = _LABELS[-1]
            labels_truncated = True

        cells: list[tuple[int, int, bool]] = []
        for row in range(top, bottom + 1):
            for col in range(left, right + 1):
                interior = left < col < right and top < row < bottom
                cells.append((row, col, interior))

        collision = any(owners[row][col] is not None for row, col, _ in cells)
        for row, col, interior in cells:
            if interior:
                canvas[row][col] = label
                owners[row][col] = draw_index
            else:
                canvas[row][col] = "-" if row in (top, bottom) else "|"
                if col in (left, right):
                    canvas[row][col] = "+"
                owners[row][col] = None
        if collision:
            canvas[top][left] = "*"

        bounds_text = node["bounds"]
        legend_line = (
            f"{label}  {node.get('id', '')}  {node.get('type', '')}  "
            f"({bounds_text.get('x')},{bounds_text.get('y')},{bounds_text.get('width')},{bounds_text.get('height')})"
        )
        if node.get("text") is not None:
            legend_line += f'  "{_text_for_legend(node["text"])}"'
        legend.append(legend_line)

    output = ["".join(row) for row in canvas]
    output.extend(("", "Legend:"))
    output.extend(legend)
    if labels_truncated:
        output.append("Note: label capacity exceeded; the last label was reused (truncated).")
    if no_bounds:
        output.extend(("", "No bounds:"))
        output.extend(f"{node.get('id', '')} ({node.get('type', '')})" for node in no_bounds)
    return "\n".join(output)
