"""Unit tests for the scene_tree / measure orchestration in tools.live.

These fake the bridge client so the integration logic (wireframe, diff, save,
colour sampling, serialization limits, measure dispatch) is covered without a
running Ren'Py game.
"""

import io
import json

import pytest


def _png_bytes(width, height, fill):
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    image = Image.new("RGBA", (width, height), fill)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeClient:
    def __init__(self, reply, png=None):
        self._reply = reply
        self._png = png

    def scene_tree(self, **kwargs):
        reply = json.loads(json.dumps(self._reply))  # deep copy per call
        ids = kwargs.get("ids")
        if ids:
            wanted = set(ids)
            reply["nodes"] = [n for n in reply["nodes"] if n.get("id") in wanted]
        return reply

    def screenshot(self, width=0, height=0):
        if self._png is None:
            raise RuntimeError("no frame")
        return self._png


def _node(nid, x, y, w, h, ntype="image", **extra):
    node = {
        "id": nid,
        "type": ntype,
        "layer": "master",
        "screen": None,
        "bounds": {"x": x, "y": y, "width": w, "height": h},
        "center": {"x": x + w // 2, "y": y + h // 2},
        "zorder": 0,
        "visible": True,
        "bounds_available": True,
    }
    node.update(extra)
    return node


def _reply(nodes, width=200, height=200):
    return {
        "ok": True,
        "coordinate_space": "logical",
        "window": {"width": width, "height": height},
        "detail": "semantic",
        "nodes": nodes,
        "counts": {"perceived": len(nodes), "returned": len(nodes)},
        "omitted": {"by_type": {}, "by_layer": {}},
    }


def _patch(monkeypatch, client):
    from renforge.tools import live

    monkeypatch.setattr(live, "_client", lambda project_path: client)
    return live


def test_scene_tree_wireframe_has_legend(monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("hero", 10, 10, 40, 40)])))
    out = live.scene_tree("proj", format="wireframe")
    assert out["ok"] is True
    assert "wireframe" in out
    assert "hero" in out["wireframe"]


def test_scene_tree_truncates_to_max_items(monkeypatch):
    nodes = [_node("n%d" % i, i, i, 5, 5) for i in range(10)]
    live = _patch(monkeypatch, _FakeClient(_reply(nodes)))
    out = live.scene_tree("proj", max_items=4)
    assert out["truncated"] is True
    assert len(out["nodes"]) == 4


def test_scene_tree_save_then_diff_detects_move(tmp_path, monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("hero", 0, 0, 10, 10)])))
    saved = live.scene_tree(str(tmp_path), save_as="before")
    assert saved.get("saved_as")

    _patch(monkeypatch, _FakeClient(_reply([_node("hero", 30, 0, 10, 10)])))
    out = live.scene_tree(str(tmp_path), diff_against="before")
    diff = out["diff"]
    assert diff["against"] == "before"
    moved = [c for c in diff["changed"] if c["id"] == "hero" and "moved" in c["changes"]]
    assert moved and moved[0]["moved"]["dx"] == 30


def test_scene_tree_diff_missing_snapshot(monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("a", 0, 0, 5, 5)])))
    out = live.scene_tree("proj", diff_against="nope")
    assert "diff_error" in out


def test_scene_tree_color_samples_frame(monkeypatch):
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    png = _png_bytes(200, 200, (200, 30, 30, 255))
    live = _patch(monkeypatch, _FakeClient(_reply([_node("bg", 0, 0, 200, 200)]), png=png))
    out = live.scene_tree("proj", include=["color"])
    color = out["nodes"][0].get("color")
    assert color and color["dominant"] == "#C81E1E"


def test_measure_align_reports_row_alignment(monkeypatch):
    nodes = [_node("a", 0, 0, 20, 20, ntype="button"), _node("b", 50, 0, 20, 20, ntype="button")]
    live = _patch(monkeypatch, _FakeClient(_reply(nodes)))
    out = live.measure("proj", action="align", targets=["a", "b"], tolerance=1)
    assert out["ok"] is True
    assert out["result"]["top"] == 0 and out["result"]["bottom"] == 0
    assert out["pass"] is False  # not left-aligned: left edge spread is 50px


def test_measure_gap_accepts_literal_bounds(monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([])))
    a = {"x": 0, "y": 0, "width": 10, "height": 10}
    b = {"x": 30, "y": 0, "width": 10, "height": 10}
    out = live.measure("proj", action="gap", targets=[a, b])
    assert out["result"]["horizontal"] == 20


def test_measure_unknown_target_id_errors(monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("a", 0, 0, 5, 5)])))
    out = live.measure("proj", action="gap", targets=["a", "ghost"])
    assert out["ok"] is False and "ghost" in out["error"]


def test_measure_contrast_on_split_region(monkeypatch):
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    image = Image.new("RGBA", (200, 200), (0, 0, 0, 255))
    for x in range(100, 200):
        for y in range(200):
            image.putpixel((x, y), (255, 255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    nodes = [_node("box", 0, 0, 200, 200, ntype="text", text="hi")]
    live = _patch(monkeypatch, _FakeClient(_reply(nodes), png=buffer.getvalue()))
    out = live.measure("proj", action="contrast", targets=["box"])
    assert out["ok"] is True
    assert out["result"]["ratio"] > 10  # black vs white is a high WCAG ratio
