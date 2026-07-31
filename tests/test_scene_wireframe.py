from renforge.scene_wireframe import render_wireframe


def _node(node_id: str, node_type: str = "image", **bounds):
    return {
        "id": node_id,
        "type": node_type,
        "bounds": bounds,
        "bounds_available": True,
        "zorder": 0,
    }


def test_canvas_dimensions_follow_width_and_aspect() -> None:
    result = render_wireframe([], {"width": 1600, "height": 900}, width=40)
    lines = result.splitlines()
    assert len(lines[0]) == 40
    assert len(lines) >= 10
    assert len(lines[:10]) == 10


def test_node_box_is_drawn_at_mapped_location() -> None:
    result = render_wireframe(
        [_node("hero", x=25, y=10, width=50, height=20)],
        {"width": 100, "height": 50},
        width=40,
    )
    canvas = result.splitlines()[:10]
    assert canvas[2][10] == "+"
    assert canvas[2][30] == "+"
    assert canvas[6][10] == "+"
    assert canvas[3][11] == "A"


def test_legend_maps_label_to_node_details() -> None:
    result = render_wireframe(
        [_node("title", "text", x=10, y=5, width=30, height=20) | {"text": "A title"}],
        {"width": 100, "height": 50},
        width=40,
    )
    assert 'A  title  text  (10,5,30,20)  "A title"' in result


def test_higher_z_overlap_marks_collision() -> None:
    lower = _node("background", x=0, y=0, width=80, height=80)
    higher = _node("panel", x=20, y=20, width=50, height=40) | {"zorder": 1}
    result = render_wireframe([higher, lower], {"width": 100, "height": 100}, width=40)
    canvas = result.splitlines()[:20]
    assert canvas[4][8] == "*"
    assert "B  panel" in result


def test_unmeasurable_node_is_reported_under_no_bounds() -> None:
    node = _node("missing", "button", x=0, y=0, width=10, height=10) | {
        "bounds_available": False,
        "bounds": None,
    }
    result = render_wireframe([node], {"width": 100, "height": 100}, width=40)
    assert "No bounds:" in result
    assert "missing (button)" in result
    assert "Legend:\n\nNo bounds:" in result


def test_empty_input_does_not_raise() -> None:
    result = render_wireframe([], {"width": 0, "height": 0}, width=1)
    assert result.splitlines()[-1] == "Legend:"
    assert len(result.splitlines()[0]) == 20
