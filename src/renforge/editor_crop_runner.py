"""Live proof for editing a target under pure ``Transform(crop=)`` (issue #45).

Identity measured on Ren'Py 8.5.3: ``Crop(rect, child)`` is a constructor that
returns ``Transform(child, crop=rect)``. Live ancestry reports ``type``
``Transform`` and ``crop_state`` ``transform_crop`` (pure) or
``transform_crop_composite`` when rotate/zoom are non-default.

The central risk was visible geometry: a crop might leave a focus rect intact
while paint is partial or absent. Measured on Ren'Py 8.5.3 for pure crop: focus
rects are already clipped to the crop window (partial height shorter than a
natural sibling; fully clipped controls absent from ``list_ui_elements``). That
makes the existing screen-space arithmetic crop-agnostic for pure
``transform_crop``, the same way issue #44 found for viewport scroll.

Only pure ``transform_crop`` is unlocked. Crop+rotate and crop+zoom stay locked
with ``TRANSFORM_CROP_COMPOSITE_UNSUPPORTED`` (issue #46).
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_editable_statement
from renforge.editor_live_common import (
    center as _center,
    inject_editor_live_resources,
    observe_selected as _observe_selected,
    select_lock,
    sha256_file as _sha256_file,
    wait_bounds,
)
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)

FIXTURE_SCREEN = "renforge_editor_crop_fixture"
TARGET_ID = "crop_target"

# Fixture layout (screen space): crop window at (200, 160) size 300×200.
CROP_WINDOW = {"x": 200, "y": 160, "width": 300, "height": 200}


def inject_editor_crop_resources(project_root: Path) -> dict[str, str]:
    return inject_editor_live_resources(
        project_root,
        editor_basename="editor_crop",
        fixture_filename="renforge_editor_crop_fixture.rpy",
    )


def _target_line_with_offset(source_text: str) -> tuple[str, int]:
    offset = 0
    for line in source_text.splitlines(keepends=True):
        if f'id "{TARGET_ID}"' in line and line.lstrip().startswith("textbutton "):
            analyze_editable_statement(line, expected_widget_id=TARGET_ID)
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing crop textbutton line for {TARGET_ID!r}")


def _parse_xy(source_text: str) -> dict[str, int]:
    line, _ = _target_line_with_offset(source_text)
    x = re.search(r"\bxpos\s+(-?\d+)", line)
    y = re.search(r"\bypos\s+(-?\d+)", line)
    if x is None or y is None:
        raise AssertionError(f"target line missing literal xpos/ypos: {line!r}")
    return {"x": int(x.group(1)), "y": int(y.group(1))}


def _independent_expected_after_patch(before_text: str, *, x: int, y: int) -> str:
    line, offset = _target_line_with_offset(before_text)
    patched = re.sub(r"(\bxpos\s+)-?\d+", rf"\g<1>{int(x)}", line, count=1)
    patched = re.sub(r"(\bypos\s+)-?\d+", rf"\g<1>{int(y)}", patched, count=1)
    if patched == line:
        raise AssertionError("independent patch constructor did not change the coordinates")
    return f"{before_text[:offset]}{patched}{before_text[offset + len(line) :]}"


def _outside_coordinate_spans_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text)
    after_line, _ = _target_line_with_offset(after_text)
    normalise = lambda line: re.sub(  # noqa: E731
        r"(\bypos\s+)-?\d+",
        r"\1__Y__",
        re.sub(r"(\bxpos\s+)-?\d+", r"\1__X__", line, count=1),
        count=1,
    )
    if normalise(before_line) != normalise(after_line):
        return False
    return before_text.replace(before_line, "", 1) == after_text.replace(after_line, "", 1)


def _rect_intersection(a: dict[str, int], b: dict[str, int]) -> dict[str, int] | None:
    x0 = max(int(a["x"]), int(b["x"]))
    y0 = max(int(a["y"]), int(b["y"]))
    x1 = min(int(a["x"]) + int(a["width"]), int(b["x"]) + int(b["width"]))
    y1 = min(int(a["y"]) + int(a["height"]), int(b["y"]) + int(b["height"]))
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _focus_vs_crop(bounds: dict[str, int]) -> dict[str, Any]:
    """Independent visible-geometry estimate: focus AABB ∩ authored crop window.

    This is not derived from the focus rect alone as "fully painted"; it
    intersects focus with the known crop window from the fixture layout.
    """
    focus = {
        "x": int(bounds["x"]),
        "y": int(bounds["y"]),
        "width": int(bounds["width"]),
        "height": int(bounds["height"]),
    }
    visible = _rect_intersection(focus, CROP_WINDOW)
    fully_inside = (
        focus["x"] >= CROP_WINDOW["x"]
        and focus["y"] >= CROP_WINDOW["y"]
        and focus["x"] + focus["width"] <= CROP_WINDOW["x"] + CROP_WINDOW["width"]
        and focus["y"] + focus["height"] <= CROP_WINDOW["y"] + CROP_WINDOW["height"]
    )
    center_pt = _center(focus)
    center_in_crop = (
        CROP_WINDOW["x"] <= center_pt[0] < CROP_WINDOW["x"] + CROP_WINDOW["width"]
        and CROP_WINDOW["y"] <= center_pt[1] < CROP_WINDOW["y"] + CROP_WINDOW["height"]
    )
    return {
        "focus": focus,
        "crop_window": dict(CROP_WINDOW),
        "visible_intersection": visible,
        "focus_fully_inside_crop": fully_inside,
        "focus_center_in_crop": center_in_crop,
        "clipped_height_px": (
            None
            if visible is None
            else int(focus["height"]) - int(visible["height"])
        ),
    }


def measure_visible_geometry(client: Any) -> dict[str, Any]:
    """Measure focus vs crop for fully-visible, partial, and full-clip targets.

    Measured on Ren'Py 8.5.3: ``Transform(crop=)`` already clips focus rects to
    the crop window. A partially clipped textbutton reports a reduced height
    (e.g. 15px) versus a fully-visible sibling (~35px), and a fully clipped
    control is absent from ``list_ui_elements``. Focus is therefore not an
    unclipped layout box under pure crop — it tracks visible geometry.
    """
    from renforge.editor_live_common import list_ui_info

    info = list_ui_info(client, FIXTURE_SCREEN)
    listed_ids = {
        str(el.get("id"))
        for el in (info.get("elements") or [])
        if isinstance(el, dict) and el.get("id")
    }

    target = wait_bounds(client, TARGET_ID, fixture_screen=FIXTURE_SCREEN)
    outside = wait_bounds(client, "crop_outside", fixture_screen=FIXTURE_SCREEN)
    partial = wait_bounds(client, "crop_partial", fixture_screen=FIXTURE_SCREEN)

    # Full-clip is expected absent from list_ui (measured on 8.5.3).
    fullclip_listed = "crop_fullclip" in listed_ids
    fullclip_bounds = None
    if fullclip_listed:
        fullclip_bounds = wait_bounds(client, "crop_fullclip", fixture_screen=FIXTURE_SCREEN)

    # Independent paint sample just inside vs just outside the crop edge.
    paint = _sample_paint_near_partial(client, partial)

    natural_height = int(outside["height"])  # same statement shape, no crop
    partial_height = int(partial["height"])
    return {
        "listed_ids": sorted(listed_ids),
        "natural_height_outside": natural_height,
        "target": _focus_vs_crop(target),
        "partial": {
            **_focus_vs_crop(partial),
            "paint": paint,
            "natural_height_reference": natural_height,
            "focus_height": partial_height,
            # Engine-clipped focus: reported height is strictly less than a
            # fully-visible sibling of the same statement shape.
            "focus_shorter_than_natural": partial_height < natural_height - 1,
            "height_delta_from_natural": natural_height - partial_height,
        },
        "fullclip": {
            "listed_in_list_ui": fullclip_listed,
            "bounds": fullclip_bounds,
            "focus_vs_crop": _focus_vs_crop(fullclip_bounds) if fullclip_bounds else None,
        },
    }


def _sample_paint_near_partial(client: Any, partial: dict[str, int]) -> dict[str, Any]:
    """Sample PNG pixels at the partial centre and just outside the crop edge."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - test env always has Pillow via live stack
        return {"error": "PIL_UNAVAILABLE"}

    png = client.screenshot()
    image = Image.open(io.BytesIO(png)).convert("RGB")
    crop_bottom = CROP_WINDOW["y"] + CROP_WINDOW["height"]
    cx, cy = _center(partial)
    # Point just outside the crop window below the partial button.
    outside = (cx, crop_bottom + 8)
    inside = (cx, min(cy, crop_bottom - 2))

    def _px(pt: tuple[int, int]) -> list[int]:
        x = max(0, min(image.width - 1, int(pt[0])))
        y = max(0, min(image.height - 1, int(pt[1])))
        return list(image.getpixel((x, y)))

    return {
        "image_size": [image.width, image.height],
        "inside_crop_point": list(inside),
        "outside_crop_point": list(outside),
        "inside_rgb": _px(inside),
        "outside_rgb": _px(outside),
        "pixels_differ": _px(inside) != _px(outside),
    }


def run_editor_crop_live_scenario(
    client: Any,
    *,
    fixture_path: Path,
) -> dict[str, Any]:
    """Seven-step live proof for a pure-crop child, plus visible-geometry matrix."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = _sha256_file(fixture_path)
    baseline_text = baseline_bytes.decode("utf-8")
    baseline_position = _parse_xy(baseline_text)
    report["fixture_before"] = {"sha256": baseline_sha, "position": baseline_position}

    _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )

    # Visible geometry BEFORE any unlock-dependent write.
    # Measured: pure Transform(crop=) already clips focus rects to the crop
    # window (partial height < natural sibling; fullclip absent from list_ui).
    report["visible_geometry"] = measure_visible_geometry(client)
    target_vis = report["visible_geometry"]["target"]
    if not target_vis["focus_fully_inside_crop"]:
        raise AssertionError(
            f"crop_target focus is not fully inside the crop window: {target_vis!r}"
        )
    partial_vis = report["visible_geometry"]["partial"]
    if not partial_vis["focus_shorter_than_natural"]:
        raise AssertionError(
            "crop_partial focus height was expected shorter than a natural sibling "
            f"(engine-clipped focus): {partial_vis!r}"
        )
    if not partial_vis["focus_fully_inside_crop"]:
        raise AssertionError(
            "crop_partial focus should be clipped into the crop window: "
            f"{partial_vis!r}"
        )
    if report["visible_geometry"]["fullclip"]["listed_in_list_ui"]:
        raise AssertionError(
            "crop_fullclip was expected absent from list_ui; "
            f"got {report['visible_geometry']['fullclip']!r}"
        )

    # Lock matrix.
    report["locks"] = {
        "computed": select_lock(
            client, "crop_computed", "YPOS_LITERAL_REQUIRED", fixture_screen=FIXTURE_SCREEN
        ),
        "container": select_lock(
            client,
            "crop_container",
            "CONTAINER_POSITION_UNSUPPORTED",
            fixture_screen=FIXTURE_SCREEN,
        ),
        "crop_with_rotate": select_lock(
            client,
            "crop_with_rotate",
            "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED",
            fixture_screen=FIXTURE_SCREEN,
        ),
        "crop_with_zoom": select_lock(
            client,
            "crop_with_zoom",
            "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED",
            fixture_screen=FIXTURE_SCREEN,
        ),
    }

    # Outside control: same adapter, no crop ancestor — must remain moveable.
    outside_bounds = wait_bounds(client, "crop_outside", fixture_screen=FIXTURE_SCREEN)
    outside_center = _center(outside_bounds)
    _require_ok(
        client.request(
            "editor_task0_select",
            {"x": outside_center[0], "y": outside_center[1]},
        ),
        "outside select",
    )
    outside_status = _wait_for_status(
        client,
        lambda status: status.get("selected_widget_id") == "crop_outside"
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="outside analysis",
    )
    report["outside"] = {
        "lock_reason": outside_status.get("selected_lock_reason"),
        "move": outside_status.get("selected_lock_reason") in (None, ""),
    }

    # Step 1: resolve the fully-visible pure-crop target.
    target_bounds = wait_bounds(client, TARGET_ID, fixture_screen=FIXTURE_SCREEN)
    target_center = _center(target_bounds)
    select = _require_ok(
        client.request(
            "editor_task0_select",
            {"x": target_center[0], "y": target_center[1]},
        ),
        "target select",
    )
    observation = select.get("observation") or {}
    if observation.get("measurement_method") != "focus_list":
        raise AssertionError(f"select observation not focus_list: {observation!r}")

    analysis_status = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="crop analysis",
    )
    source_key = analysis_status.get("current_source_key") or {}
    runtime_key = analysis_status.get("selected_runtime_key") or {}
    ancestry = runtime_key.get("ancestry") or []
    transform_crop_count = sum(
        1 for node in ancestry if isinstance(node, dict) and node.get("crop_state") == "transform_crop"
    )
    composite_count = sum(
        1
        for node in ancestry
        if isinstance(node, dict) and node.get("crop_state") == "transform_crop_composite"
    )
    transform_types = [
        node.get("type")
        for node in ancestry
        if isinstance(node, dict) and node.get("crop_state") in {"transform_crop", "transform_crop_composite"}
    ]
    host_move = bool(analysis_status.get("current_analysis_id")) and analysis_status.get(
        "selected_lock_reason"
    ) in (None, "")
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "lock_reason": analysis_status.get("selected_lock_reason"),
        "move": host_move,
        "measurement_method": observation.get("measurement_method"),
        "transform_crop_ancestor_count": transform_crop_count,
        "transform_crop_composite_count": composite_count,
        "crop_ancestor_types": transform_types,
        "ancestry_crop_states": [
            node.get("crop_state") for node in ancestry if isinstance(node, dict)
        ],
    }
    if host_move is not True:
        raise AssertionError(f"host did not unlock move inside pure crop: {analysis_status!r}")
    if transform_crop_count != 1 or composite_count != 0:
        raise AssertionError(f"unexpected crop ancestry shape: {report['resolve']!r}")
    if any(t != "Transform" for t in transform_types):
        raise AssertionError(
            f"Crop sugar must surface as Transform, got types={transform_types!r}"
        )

    original = analysis_status.get("selected_original_position")
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        original = [baseline_position["x"], baseline_position["y"]]
    requested_before = [int(original[0]), int(original[1])]

    before_obs = _observe_selected(client)
    before_rect = before_obs.get("rect") or []
    bounds_before = [int(before_rect[0]), int(before_rect[1])]
    frame_before = before_obs.get("frame_id")
    vis_before = _focus_vs_crop(
        {
            "x": int(before_rect[0]),
            "y": int(before_rect[1]),
            "width": int(before_rect[2]) if len(before_rect) > 2 else target_bounds["width"],
            "height": int(before_rect[3]) if len(before_rect) > 3 else target_bounds["height"],
        }
    )

    # Step 2: preview. Keep the target fully inside the crop window.
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 12}), "nudge right")
    _require_ok(client.request("editor_task0_key", {"key": "down", "repeat": 8}), "nudge down")
    preview_status = _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_position"), (list, tuple))
        and len(status.get("preview_position") or []) == 2
        and list(status.get("preview_position") or []) != requested_before,
        timeout=8.0,
        poll_name="crop preview moved",
    )
    requested_after = [
        int(preview_status["preview_position"][0]),
        int(preview_status["preview_position"][1]),
    ]
    after_obs = _observe_selected(client)
    after_rect = after_obs.get("rect") or []
    bounds_after = [int(after_rect[0]), int(after_rect[1])]
    frame_after = after_obs.get("frame_id")
    if not frame_before or not frame_after or frame_before == frame_after:
        raise AssertionError(
            f"preview observations need distinct frame_ids: {frame_before!r} / {frame_after!r}"
        )
    requested_delta = [requested_after[axis] - requested_before[axis] for axis in (0, 1)]
    observed_delta = [bounds_after[axis] - bounds_before[axis] for axis in (0, 1)]
    if any(abs(observed_delta[axis] - requested_delta[axis]) > 1 for axis in (0, 1)):
        raise AssertionError(
            "focus_list preview bounds disagree with requested movement under crop: "
            f"requested={requested_delta!r}, observed={observed_delta!r}"
        )
    vis_after = _focus_vs_crop(
        {
            "x": int(after_rect[0]),
            "y": int(after_rect[1]),
            "width": int(after_rect[2]) if len(after_rect) > 2 else target_bounds["width"],
            "height": int(after_rect[3]) if len(after_rect) > 3 else target_bounds["height"],
        }
    )
    if not vis_after["focus_fully_inside_crop"]:
        raise AssertionError(f"preview moved target outside crop window: {vis_after!r}")
    report["preview"] = {
        "bounds_before": bounds_before,
        "bounds_after": bounds_after,
        "requested_before": requested_before,
        "requested_after": requested_after,
        "requested_delta": requested_delta,
        "observed_delta": observed_delta,
        "visible_before": vis_before,
        "visible_after": vis_after,
    }

    # Step 3: patch through Save.
    pre_save_bytes = fixture_path.read_bytes()
    pre_save_text = pre_save_bytes.decode("utf-8")
    pre_save_sha = hashlib.sha256(pre_save_bytes).hexdigest()
    generation_before = _source_generation(analysis_status)
    _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "crop save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") in ("Reload committed", "Reload failed"),
        timeout=60.0,
        poll_name="crop save settled",
    )
    if save_status.get("status_text") != "Reload committed":
        raise AssertionError(f"save did not commit: {save_status.get('save_error')!r}")
    if _source_generation(save_status) != generation_before + 1:
        raise AssertionError(f"unexpected script generation after save: {save_status!r}")
    post_save_bytes = fixture_path.read_bytes()
    post_save_text = post_save_bytes.decode("utf-8")
    post_save_sha = hashlib.sha256(post_save_bytes).hexdigest()
    source_position_after = _parse_xy(post_save_text)
    # preview_position is screen-space; authored value is child-space. Under a
    # crop Transform the origins differ — compare deltas only (#44 trap).
    expected_source_position = {
        "x": baseline_position["x"] + requested_delta[0],
        "y": baseline_position["y"] + requested_delta[1],
    }
    if source_position_after != expected_source_position:
        raise AssertionError(
            "source patch disagrees with the requested preview delta: "
            f"expected={expected_source_position!r}, observed={source_position_after!r}"
        )
    if post_save_text != _independent_expected_after_patch(
        pre_save_text,
        x=expected_source_position["x"],
        y=expected_source_position["y"],
    ):
        raise AssertionError("patched fixture bytes disagree with independent expected content")
    if not _outside_coordinate_spans_identical(pre_save_text, post_save_text):
        raise AssertionError("source patch changed bytes outside the xpos/ypos spans")
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_position_after": source_position_after,
        "outside_coordinate_spans_identical": True,
        "matches_independent_expected": True,
    }

    # Step 4: reload.
    report["reload"] = {
        "ok": True,
        "status_text": save_status.get("status_text"),
        "generation_delta": _source_generation(save_status) - generation_before,
    }

    # Step 5: pixel agreement.
    successor = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="crop post-save rebind",
    )
    reload_obs = _observe_selected(client)
    reload_rect = reload_obs.get("rect") or []
    reload_bounds = [int(reload_rect[0]), int(reload_rect[1])]
    pixel_delta = [reload_bounds[axis] - bounds_after[axis] for axis in (0, 1)]
    if any(abs(value) > 1 for value in pixel_delta):
        raise AssertionError(
            "post-reload focus rect disagrees with post-preview focus rect: "
            f"preview={bounds_after!r}, reload={reload_bounds!r}"
        )
    report["pixel_agreement"] = {
        "preview_after": bounds_after,
        "reload_after": reload_bounds,
        "delta": pixel_delta,
    }

    # Step 6: rebinding.
    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id")
        not in (None, analysis_status.get("current_analysis_id"))
        and successor.get("selected_lock_reason") in (None, ""),
        "widget_id": successor.get("selected_widget_id"),
    }
    if report["rebinding"]["ok"] is not True:
        raise AssertionError(f"rebinding failed under crop: {report['rebinding']!r}")

    # Visible geometry after movement: still fully inside.
    report["visible_geometry_after"] = measure_visible_geometry(client)
    if not report["visible_geometry_after"]["target"]["focus_fully_inside_crop"]:
        raise AssertionError(
            "target left the crop window after the write chain: "
            f"{report['visible_geometry_after']['target']!r}"
        )

    # Step 7: byte-identical undo.
    fixture_path.write_bytes(baseline_bytes)
    restored_bytes = fixture_path.read_bytes()
    report["byte_identical_undo"] = {
        "matches_baseline": restored_bytes == baseline_bytes,
        "patched_differed": post_save_bytes != baseline_bytes,
    }
    if restored_bytes != baseline_bytes:
        raise AssertionError("crop byte-identical undo did not restore the baseline")
    return report
