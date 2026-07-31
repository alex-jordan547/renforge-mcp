from renforge.scene_diff import diff_scenes


def _node(node_id: str, **overrides):
    node = {
        "id": node_id,
        "type": "image",
        "bounds": {"x": 10, "y": 20, "width": 30, "height": 40},
        "bounds_available": True,
        "zorder": 0,
        "visible": True,
    }
    node.update(overrides)
    return node


def _scene(*nodes):
    return {"window": {"width": 100, "height": 100}, "nodes": list(nodes)}


def test_added_and_removed_nodes_are_compact_and_sorted():
    result = diff_scenes(
        _scene(_node("old", type="text")),
        _scene(
            _node("new-b", type="button", bounds=None),
            _node("new-a", type="text"),
        ),
    )

    assert result["added"] == [
        {"id": "new-a", "type": "text", "bounds": {"x": 10, "y": 20, "width": 30, "height": 40}},
        {"id": "new-b", "type": "button", "bounds": None},
    ]
    assert result["removed"] == [
        {"id": "old", "type": "text", "bounds": {"x": 10, "y": 20, "width": 30, "height": 40}}
    ]


def test_moved_respects_strict_threshold():
    before = _scene(_node("sprite"))
    after = _scene(_node("sprite", bounds={"x": 11, "y": 20, "width": 30, "height": 40}))

    assert diff_scenes(before, after, move_threshold=1)["changed"] == []
    assert diff_scenes(before, after, move_threshold=0)["changed"] == [
        {
            "id": "sprite",
            "changes": ["moved"],
            "moved": {"dx": 1, "dy": 0},
        }
    ]


def test_resized_reports_width_and_height_delta():
    result = diff_scenes(
        _scene(_node("panel")),
        _scene(_node("panel", bounds={"x": 10, "y": 20, "width": 33, "height": 38})),
        move_threshold=1,
    )

    assert result["changed"] == [
        {
            "id": "panel",
            "changes": ["resized"],
            "resized": {"dw": 3, "dh": -2},
        }
    ]


def test_text_color_and_zorder_changes_are_reported():
    before_node = _node(
        "label",
        text="Before",
        color={"dominant": "#112233", "sampled": True},
        zorder=2,
    )
    after_node = _node(
        "label",
        text="After",
        color={"dominant": "#AABBCC", "sampled": True},
        zorder=4,
    )

    result = diff_scenes(_scene(before_node), _scene(after_node))

    assert result["changed"] == [
        {
            "id": "label",
            "changes": ["color_changed", "text_changed", "z_changed"],
            "color_changed": {"from": "#112233", "to": "#AABBCC"},
            "text_changed": {"from": "Before", "to": "After"},
            "z_changed": {"from": 2, "to": 4},
        }
    ]


def test_unchanged_node_is_counted():
    node = _node("steady")

    assert diff_scenes(_scene(node), _scene(dict(node))) == {
        "added": [],
        "removed": [],
        "changed": [],
        "unchanged": 1,
    }


def test_null_or_unavailable_bounds_do_not_crash_or_report_geometry_changes():
    before = _scene(_node("unknown", bounds=None, bounds_available=False))
    after = _scene(_node("unknown", bounds={"x": 99, "y": 98, "width": 97, "height": 96}))

    result = diff_scenes(before, after)

    assert result["changed"] == []
    assert result["unchanged"] == 1
