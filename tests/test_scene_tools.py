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
        self.calls = []

    def scene_tree(self, **kwargs):
        self.calls.append(kwargs)
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
    client = _FakeClient(_reply(nodes))
    live = _patch(monkeypatch, client)
    out = live.scene_tree("proj", types=["image"], max_items=4)
    assert out["truncated"] is True
    assert len(out["nodes"]) == 4
    assert "max_nodes" not in client.calls[-1]


def test_scene_tree_limits_nodes_before_derived_outputs(tmp_path, monkeypatch):
    nodes = [_node("n%d" % i, i * 10, 0, 5, 5) for i in range(3)]
    live = _patch(monkeypatch, _FakeClient(_reply(nodes)))

    out = live.scene_tree(
        str(tmp_path),
        format="wireframe",
        save_as="limited",
        max_items=1,
    )

    assert "n0" in out["wireframe"]
    assert "n1" not in out["wireframe"]
    saved = json.loads((tmp_path / ".renforge" / "scenes" / "limited.json").read_text())
    assert [node["id"] for node in saved["nodes"]] == ["n0"]


def test_scene_tree_applies_depth_and_byte_limits(monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("hero", 0, 0, 10, 10)])))

    depth_limited = live.scene_tree("proj", max_output_depth=2)
    byte_limited = live.scene_tree("proj", max_output_bytes=128)

    assert "max_depth" in json.dumps(depth_limited)
    assert byte_limited["__reason__"] == "max_output_bytes"


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


def test_scene_tree_rejects_invalid_snapshot_names(tmp_path, monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("hero", 0, 0, 10, 10)])))

    out = live.scene_tree(str(tmp_path), save_as="a/b")

    assert out["ok"] is False
    assert "snapshot name" in out["error"]


def test_scene_tree_refuses_symlinked_snapshot_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".renforge").symlink_to(outside, target_is_directory=True)
    live = _patch(monkeypatch, _FakeClient(_reply([_node("hero", 0, 0, 10, 10)])))

    out = live.scene_tree(str(project), save_as="before")

    assert out["ok"] is False
    assert "symlink" in out["error"].lower()
    assert list(outside.iterdir()) == []


def test_scene_tree_refuses_symlinked_snapshot_file(tmp_path, monkeypatch):
    scenes = tmp_path / ".renforge" / "scenes"
    scenes.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("do not overwrite", encoding="utf-8")
    (scenes / "before.json").symlink_to(outside)
    live = _patch(monkeypatch, _FakeClient(_reply([_node("hero", 0, 0, 10, 10)])))

    out = live.scene_tree(str(tmp_path), save_as="before")

    assert out["ok"] is False
    assert "symlink" in out["error"].lower()
    assert outside.read_text(encoding="utf-8") == "do not overwrite"


def test_scene_tree_diff_missing_snapshot(tmp_path, monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("a", 0, 0, 5, 5)])))
    out = live.scene_tree(str(tmp_path), diff_against="nope")
    assert "diff_error" in out


def test_scene_tree_diff_rejects_oversized_snapshot(tmp_path, monkeypatch):
    live = _patch(monkeypatch, _FakeClient(_reply([_node("a", 0, 0, 5, 5)])))
    monkeypatch.setattr(live, "_SCENE_SNAPSHOT_MAX_BYTES", 32)
    scenes = tmp_path / ".renforge" / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "large.json").write_text('{"nodes":[' + (" " * 64) + "]}", encoding="utf-8")

    out = live.scene_tree(str(tmp_path), diff_against="large")

    assert "diff_error" in out


def test_scene_tree_refuses_to_save_oversized_snapshot(tmp_path, monkeypatch):
    node = _node("hero", 0, 0, 5, 5)
    node["text"] = "X" * 100
    live = _patch(monkeypatch, _FakeClient(_reply([node])))
    monkeypatch.setattr(live, "_SCENE_SNAPSHOT_MAX_BYTES", 64)

    out = live.scene_tree(str(tmp_path), save_as="large")

    assert out["ok"] is False
    assert "exceeds 64 bytes" in out["error"]
    assert not (tmp_path / ".renforge" / "scenes" / "large.json").exists()


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


@pytest.mark.parametrize(
    "invalid",
    [
        {"x": 0, "y": 0, "width": -1, "height": 10},
        {"x": True, "y": 0, "width": 10, "height": 10},
        {"x": 0, "y": 0, "width": float("nan"), "height": 10},
    ],
)
def test_measure_rejects_invalid_literal_bounds(monkeypatch, invalid):
    live = _patch(monkeypatch, _FakeClient(_reply([])))
    valid = {"x": 20, "y": 0, "width": 10, "height": 10}

    out = live.measure("proj", action="gap", targets=[invalid, valid])

    assert out["ok"] is False
    assert "invalid target bounds" in out["error"]


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
