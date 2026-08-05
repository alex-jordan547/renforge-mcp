from __future__ import annotations

import hashlib
import io
import shutil
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image

FIXTURE_SCREEN = "renforge_editor_task0_fixture"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_task0_fixture.rpy"
)


def inject_editor_task0_resources(
    project_root: Path,
    *,
    fixture_path: Path | None = None,
) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_task0.rpy"
    fixture_target = game_dir / "zz_renforge_editor_task0_fixture.rpy"
    source_fixture = fixture_path or FIXTURE_RESOURCE
    shutil.copyfile(EDITOR_RESOURCE, editor_target)
    shutil.copyfile(source_fixture, fixture_target)
    return {
        "editor": str(editor_target),
        "fixture": str(fixture_target),
    }


def _find_element(
    elements: list[dict[str, Any]],
    wanted_id: str,
    *,
    wanted_text: str | None = None,
) -> dict[str, Any]:
    for element in elements:
        element_id = str(element.get("id") or "")
        if element_id == wanted_id:
            return element
        if wanted_text is not None and str(element.get("text") or "") == wanted_text:
            return element
    raise AssertionError(
        f"missing expected element id {wanted_id!r} text {wanted_text!r}: {elements!r}"
    )


def _bounds_for(client: Any, wanted_id: str, *, wanted_text: str | None = None) -> dict[str, int]:
    listed = client.list_ui_elements(screen=FIXTURE_SCREEN)
    element = _find_element(listed, wanted_id, wanted_text=wanted_text)
    bounds = element.get("bounds")
    assert isinstance(bounds, dict), element
    return {
        "x": int(bounds["x"]),
        "y": int(bounds["y"]),
        "width": int(bounds["width"]),
        "height": int(bounds["height"]),
    }


def _center(bounds: dict[str, int]) -> tuple[int, int]:
    return (int(bounds["x"] + bounds["width"] // 2), int(bounds["y"] + bounds["height"] // 2))


def _open_png(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    image.load()
    return image.convert("RGB")


def _wait_for_screenshot_change(client: Any, previous: bytes, *, timeout: float = 2.0) -> bytes:
    previous_digest = hashlib.sha256(previous).digest()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = client.screenshot()
        if hashlib.sha256(current).digest() != previous_digest:
            return current
        time.sleep(0.05)
    raise AssertionError("screenshot did not change before timeout")


def _wait_for_image(client: Any, predicate, *, timeout: float = 2.0) -> Image.Image:
    deadline = time.monotonic() + timeout
    last_image: Image.Image | None = None
    while time.monotonic() < deadline:
        last_image = _open_png(client.screenshot())
        if predicate(last_image):
            return last_image
        time.sleep(0.05)
    raise AssertionError(f"screenshot predicate did not match before timeout: {last_image!r}")


def _red_score(image: Image.Image) -> int:
    count = 0
    for red, green, blue in image.getdata():
        if red >= 90 and red >= green + 35 and red >= blue + 35:
            count += 1
    return count


def _green_bbox_and_brightness(image: Image.Image) -> tuple[tuple[int, int, int, int] | None, float]:
    width, height = image.size
    xs: list[int] = []
    ys: list[int] = []
    brightness_total = 0
    brightness_count = 0
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels[x, y]
            if green >= 120 and red <= 100 and blue <= 100:
                xs.append(x)
                ys.append(y)
                brightness_total += green
                brightness_count += 1
    if not xs:
        return None, 0.0
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return bbox, (float(brightness_total) / float(brightness_count))


def _sample_rgb(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    width, height = image.size
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    pixel = image.load()
    assert pixel is not None
    value = pixel[x, y]
    return (int(value[0]), int(value[1]), int(value[2]))


def _require_ok(reply: dict[str, Any], name: str) -> dict[str, Any]:
    if reply.get("ok") is not True:
        raise AssertionError(f"{name} failed: {reply!r}")
    return reply


def _extract_widget_position(source_text: str, widget_id: str) -> dict[str, int] | None:
    lines = source_text.splitlines()
    marker = f'id "{widget_id}"'
    for index, line in enumerate(lines):
        if marker not in line:
            continue
        xpos_inline = re.search(r"\bxpos\s+(-?\d+)\b", line)
        ypos_inline = re.search(r"\bypos\s+(-?\d+)\b", line)
        if xpos_inline is not None and ypos_inline is not None:
            return {"x": int(xpos_inline.group(1)), "y": int(ypos_inline.group(1))}
        block = "\n".join(lines[max(0, index - 8) : min(len(lines), index + 20)])
        xpos_match = re.search(r"^\s*xpos\s+(-?\d+)\s*$", block, re.MULTILINE)
        ypos_match = re.search(r"^\s*ypos\s+(-?\d+)\s*$", block, re.MULTILINE)
        if xpos_match is None or ypos_match is None:
            continue
        return {"x": int(xpos_match.group(1)), "y": int(ypos_match.group(1))}
    return None


def _source_snapshot(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    target_position = _extract_widget_position(text, "task0_target")
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "position": target_position,
        "positions": {
            "task0_target": target_position,
            "task0_top": _extract_widget_position(text, "task0_top"),
        },
    }


def _source_generation(status: dict[str, Any]) -> int:
    generation = status.get("script_generation")
    try:
        return int(generation)
    except Exception:
        return 0


def _wait_for_status(
    client: Any,
    predicate,
    *,
    timeout: float,
    sleep_s: float = 0.05,
    poll_name: str = "status",
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = _require_ok(client.request("editor_task0_status"), poll_name)
        last_status = status
        if predicate(status):
            return status
        time.sleep(sleep_s)
    raise AssertionError(f"{poll_name} timeout: {last_status!r}")


def run_editor_task0_live_scenario(
    client: Any,
    *,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    target_fixture = (
        fixture_path
        if fixture_path is not None
        else Path(__file__).resolve().parents[2]
        / "tests"
        / "live_fixtures"
        / "renforge_editor_task0_fixture.rpy"
    )
    report["fixture_before"] = _source_snapshot(target_fixture)

    start = _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    report["start"] = start
    report["save_enabled"] = bool(start.get("save_enabled"))

    base_elements_info = client.list_ui_elements_info(screen=FIXTURE_SCREEN)
    base_elements = base_elements_info.get("elements") or []
    target = _find_element(base_elements, "task0_target", wanted_text="MOVE ME")
    top = _find_element(base_elements, "task0_top", wanted_text="OVERLAP TOP")
    anchor = _find_element(base_elements, "task0_anchor", wanted_text="ANCHOR")
    clipped = _find_element(base_elements, "task0_clipped", wanted_text="CLIPPED")
    dupe = _find_element(base_elements, "task0_dupe_target", wanted_text="DUPE A")
    target_center = _center(target["bounds"])
    top_center = _center(top["bounds"])
    target_pick = (
        int(target["bounds"]["x"]) + 10,
        int(target["bounds"]["y"] + target["bounds"]["height"] - 2),
    )
    clipped_center = _center(clipped["bounds"])
    dupe_pick = (
        int(dupe["bounds"]["x"]) + 3,
        int(dupe["bounds"]["y"] + dupe["bounds"]["height"] - 2),
    )
    report["frame_before"] = base_elements_info.get("frame_id")
    report["target_before"] = target["bounds"]

    top_select = _require_ok(
        client.request("editor_task0_select", {"x": top_center[0], "y": top_center[1]}),
        "top select",
    )
    report["top_select_widget"] = top_select.get("selected", {}).get("widget_id")

    target_select = _require_ok(
        client.request("editor_task0_select", {"x": target_pick[0], "y": target_pick[1]}),
        "target select",
    )
    report["target_select_widget"] = target_select.get("selected", {}).get("widget_id")
    first_observation = target_select.get("observation") or {}
    analysis_status = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and not status.get("selected_lock_reason")
        and not bool(status.get("save_enabled")),
        timeout=6.0,
        poll_name="analysis wait",
    )
    report["analysis_after_select"] = analysis_status
    if isinstance(first_observation, dict) and isinstance(first_observation.get("runtime_key"), dict):
        observe_by_target = _require_ok(
            client.request(
                "editor_observe_target",
                {"runtime_key": first_observation.get("runtime_key")},
            ),
            "editor_observe_target",
        )
        report["observe_target"] = observe_by_target
        unique_rebind_key = dict(first_observation.get("runtime_key") or {})
        unique_rebind_key.pop("instance_discriminator", None)
        report["unique_rebind"] = client.request(
            "editor_attest_targets",
            {
                "transaction_id": "preflight-unique-rebind",
                "script_generation": _source_generation(analysis_status),
                "expected_targets": [
                    {
                        "source_key": analysis_status.get("current_source_key"),
                        "runtime_key": unique_rebind_key,
                        "position": [int(target["bounds"]["x"]), int(target["bounds"]["y"])],
                    }
                ],
            },
        )

    def layout_snapshot() -> dict[str, Any]:
        return client.eval_expr(
            "{'layout': _renforge_editor_layout_mode(), "
            "'view': _renforge_editor_view_mode(), "
            "'transforms': _renforge_editor_layout_transform_count(), "
            "'editor_is_top': _EDITOR_LAYER in renpy.config.top_layers, "
            "'editor_transforms': len(renpy.config.layer_transforms.get(_EDITOR_LAYER, []))}"
        )

    def click_editor_control(widget_id: str, name: str) -> None:
        _require_ok(
            client.click_element(id=widget_id, screen="_renforge_editor_overlay"),
            name,
        )

    click_editor_control("rf_toolbar_layout_docked", "switch docked")
    docked_point = client.eval_expr(
        f"list(_renforge_editor_canvas_to_screen_point({target_center[0]}, {target_center[1]}))"
    )
    docked_select = client.request(
        "editor_task0_select",
        {
            "x": int(round(docked_point[0])),
            "y": int(round(docked_point[1])),
            "coordinate_space": "screen",
        },
    )
    if docked_select.get("ok") is not True:
        raise AssertionError(
            f"docked transformed select failed at {docked_point!r}: "
            f"{docked_select!r}"
        )
    docked = layout_snapshot()
    click_editor_control("rf_toolbar_view_preview", "switch preview")
    preview = layout_snapshot()
    click_editor_control("rf_toolbar_view_edit", "restore edit")
    redocked = layout_snapshot()
    click_editor_control("rf_toolbar_layout_overlay", "restore overlay")
    report["docked_view_mode"] = {
        "selected": docked_select.get("selected", {}).get("widget_id"),
        "docked": docked,
        "preview": preview,
        "redocked": redocked,
        "overlay": layout_snapshot(),
    }

    clipped_select = client.request("editor_task0_select", {"x": clipped_center[0], "y": clipped_center[1]})
    report["clipped_lock"] = clipped_select.get("lock_reason")

    # Deferred: editor_live_common imports this module's polling helpers, so a
    # module-level import here would be circular.
    from renforge.editor_live_common import repeated_use_lock

    report["dupe_lock"] = repeated_use_lock(
        client,
        label="task0",
        point=(dupe_pick[0], dupe_pick[1]),
    )

    validate_unknown = client.request(
        "editor_task0_validate_runtime_key",
        {
            "runtime_key": {
                "screen": FIXTURE_SCREEN,
                "invocation_path": [FIXTURE_SCREEN],
                "widget_id": "task0_target",
                "source_location": ["game/zz_renforge_editor_task0_fixture.rpy", 18],
                "instance_discriminator": "one",
                "ancestry": [
                    {
                        "index": 0,
                        "type": "UnknownWidget",
                        "source_location": ["game/zz_renforge_editor_task0_fixture.rpy", 18],
                        "screen_owner": "game",
                        "crop_state": "none",
                        "editor_owned": False,
                    }
                ],
            }
        },
    )
    report["unknown_ancestry_lock"] = validate_unknown.get("lock_reason")
    validate_multi = client.request(
        "editor_task0_validate_runtime_key",
        {
            "runtime_key": {
                "screen": FIXTURE_SCREEN,
                "invocation_path": [FIXTURE_SCREEN],
                "widget_id": "task0_target",
                "source_location": ["game/zz_renforge_editor_task0_fixture.rpy", 18],
                "instance_discriminator": "shared",
                "ancestry": [
                    {
                        "index": 0,
                        "type": "ScreenDisplayable",
                        "source_location": ["game/zz_renforge_editor_task0_fixture.rpy", 18],
                        "screen_owner": "game",
                        "crop_state": "none",
                        "editor_owned": False,
                    }
                ],
            },
            "instance_count": 2,
        },
    )
    report["multi_instance_lock"] = validate_multi.get("lock_reason")

    _require_ok(
        client.request("editor_task0_select", {"x": target_pick[0], "y": target_pick[1]}),
        "target reselect",
    )
    analysis_ready = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and not status.get("selected_lock_reason")
        and not bool(status.get("save_enabled"))
        and status.get("selected_widget_id") == "task0_target",
        timeout=6.0,
        poll_name="analysis ready after reselect",
    )
    report["analysis_after_reselect"] = analysis_ready
    report["label_after_analysis"] = client.eval_expr(
        "_renforge_editor_label_snapshot()"
    )
    report["no_op_save"] = client.request("editor_task0_save")
    motion_base = client.eval_expr(
        "list(_renforge_editor_state().preview_position or [])"
    )
    if len(motion_base) != 2:
        raise AssertionError(f"motion drag needs a selected preview: {motion_base!r}")
    motion_start = _require_ok(
        client.eval_expr(
            f"_renforge_editor_apply_drag_from_pointer("
            f"{target_center[0]}, {target_center[1]}, True)"
        ),
        "motion drag start",
    )
    motion_move = _require_ok(
        client.eval_expr(
            f"_renforge_editor_apply_drag_from_pointer("
            f"{target_center[0] + 20}, {target_center[1]}, True)"
        ),
        "motion drag move",
    )
    motion_after = client.eval_expr(
        "{"
        "'preview': list(_renforge_editor_state().preview_position or []),"
        "'drag_active': bool(_renforge_editor_state().drag_active),"
        "'measure': _renforge_editor_measure_snapshot(),"
        "'guide': _renforge_editor_guide_snapshot(),"
        "'measure_x': renpy.get_widget('_renforge_editor_overlay', 'rf_measure_x') is not None,"
        "'measure_y': renpy.get_widget('_renforge_editor_overlay', 'rf_measure_y') is not None"
        "}"
    )
    if motion_after.get("preview") == motion_base:
        raise AssertionError(f"direct motion did not move preview: {motion_after!r}")
    if set((motion_after.get("measure") or {}).keys()) != {"dx", "dy"}:
        raise AssertionError(f"direct motion mixed measurements with guides: {motion_after!r}")
    if motion_after.get("measure_x") or motion_after.get("measure_y"):
        raise AssertionError(f"direct motion rendered a competing measurement line: {motion_after!r}")
    if motion_after.get("guide") != {"line_x": None, "line_y": None}:
        raise AssertionError(f"shift drag rendered a snap guide: {motion_after!r}")
    report["motion_drag"] = {
        "base": motion_base,
        "start": motion_start,
        "move": motion_move,
        "after": motion_after,
    }
    _require_ok(client.eval_expr("_renforge_editor_end_drag()"), "motion drag end")
    _require_ok(
        client.eval_expr(
            f"_renforge_editor_apply_preview("
            f"{int(motion_base[0])}, {int(motion_base[1])}, allow_snap=False, record=False)"
        ),
        "motion drag restore",
    )
    client.eval_expr("_renforge_editor_reset_history()")


    anchor_x = int(anchor["bounds"]["x"])
    target_y = int(target["bounds"]["y"])

    drag_snap = _require_ok(
        client.request(
            "editor_task0_drag",
            {
                "points": [
                    [target_center[0], target_center[1]],
                    [anchor_x + 5, target_center[1]],
                    [anchor_x + 7, target_center[1]],
                    [anchor_x + 12, target_center[1]],
                ],
                "shift": False,
            },
        ),
        "drag snap",
    )
    report["drag_snap"] = drag_snap

    visual_bounds = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    visual_center = _center(visual_bounds)
    visual_start = _require_ok(
        client.eval_expr(
            f"_renforge_editor_apply_drag_from_pointer("
            f"{visual_center[0]}, {visual_center[1]}, False)"
        ),
        "visual guide drag start",
    )
    visual_snap = _require_ok(
        client.eval_expr(
            f"_renforge_editor_apply_drag_from_pointer("
            f"{anchor_x + 5}, {visual_center[1]}, False)"
        ),
        "visual guide snap",
    )
    report["visual_guide_drag"] = {
        "start": visual_start,
        "snap": visual_snap,
    }
    guide_status = _require_ok(client.request("editor_task0_status"), "guide status")
    guide_x = guide_status.get("guide_x")
    guide_y = guide_status.get("guide_y")
    if not isinstance(guide_x, int) and not isinstance(guide_y, int):
        raise AssertionError(f"snap did not render a guide: {guide_status!r}")
    report["distance_badge"] = client.eval_expr("_renforge_editor_distance_snapshot()")
    guide_snapshot = client.eval_expr("_renforge_editor_guide_snapshot()")
    if guide_snapshot.get("line_x") is None and guide_snapshot.get("line_y") is None:
        raise AssertionError(f"snap guide was not bounded: {guide_snapshot!r}")
    report["guide_snapshot"] = guide_snapshot

    opacity_before = client.screenshot()
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity 1.0")
    guide_high_png = _wait_for_screenshot_change(client, opacity_before)
    guide_high = _open_png(guide_high_png)
    report["distance_badge_rendered_text"] = client.eval_expr(
        "str(getattr(renpy.get_widget('_renforge_editor_overlay', 'rf_distance_x_text'), 'text', '')) "
        "if renpy.get_widget('_renforge_editor_overlay', 'rf_distance_x_text') is not None else None"
    )
    tools_hide_click = _require_ok(
        client.click_element(id="rf_tools", screen="_renforge_editor_overlay"),
        "hide tools",
    )
    tools_hidden_png = _wait_for_screenshot_change(client, guide_high_png)
    tools_hidden_state = client.eval_expr(
        "(_renforge_editor_tools_visible(), _renforge_editor_state().active, "
        "renpy.get_widget('_renforge_editor_overlay', 'rf_guide_x') is not None, "
        "renpy.get_widget('_renforge_editor_overlay', 'rf_label') is not None, "
        "renpy.get_widget('_renforge_editor_overlay', 'rf_distance_x') is not None, "
        "renpy.get_widget('_renforge_editor_overlay', 'rf_tools') is not None)"
    )
    tools_show_click = _require_ok(
        client.click_element(id="rf_tools", screen="_renforge_editor_overlay"),
        "show tools",
    )
    guide_high_png = _wait_for_screenshot_change(client, tools_hidden_png)
    guide_high = _open_png(guide_high_png)
    report["tools_visibility"] = {
        "hide_click": tools_hide_click,
        "hidden_state": tools_hidden_state,
        "show_click": tools_show_click,
        "restored_widget": client.eval_expr(
            "renpy.get_widget('_renforge_editor_overlay', 'rf_distance_x') is not None"
        ),
    }
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 0.2}), "opacity 0.2")
    _wait_for_screenshot_change(client, guide_high_png)
    exit_status = _wait_for_status(
        client,
        lambda status: (
            isinstance(status.get("rf_exit_rect"), list)
            and len(status["rf_exit_rect"]) == 4
        ),
        timeout=2.0,
        poll_name="RF Exit bounds",
    )
    exit_bounds = exit_status["rf_exit_rect"]
    exit_border_x = int(exit_bounds[0]) + int(exit_bounds[2]) // 2
    exit_border_y = int(exit_bounds[1])
    exit_fill_x = int(exit_bounds[0]) + 4
    exit_fill_y = int(exit_bounds[1]) + 4
    guide_low = _wait_for_image(
        client,
        lambda image: (
            _sample_rgb(image, exit_border_x, exit_border_y)[2] >= 220
            and _sample_rgb(image, exit_border_x, exit_border_y)[2]
            - _sample_rgb(image, exit_border_x, exit_border_y)[0]
            >= 60
        ),
    )
    if guide_snapshot.get("line_x") is not None:
        sample_x = int(guide_snapshot["line_x"][0])
        sample_y = int(guide_snapshot["line_x"][1]) + int(guide_snapshot["line_x"][2]) // 2
    else:
        sample_x = int(guide_snapshot["line_y"][0]) + int(guide_snapshot["line_y"][2]) // 2
        sample_y = int(guide_snapshot["line_y"][1])
    guide_pixel_high = _sample_rgb(guide_high, sample_x, sample_y)
    guide_pixel_low = _sample_rgb(guide_low, sample_x, sample_y)
    report["guide_red"] = {
        "high": _red_score(guide_high),
        "low": _red_score(guide_low),
        "sample_high": guide_pixel_high,
        "sample_low": guide_pixel_low,
        "sample_point": [sample_x, sample_y],
        "swatch_high": guide_pixel_high,
        "swatch_low": guide_pixel_low,
    }
    report["rf_exit_colors_low_opacity"] = {
        "border": _sample_rgb(guide_low, exit_border_x, exit_border_y),
        "fill": _sample_rgb(guide_low, exit_fill_x, exit_fill_y),
    }
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity reset")
    _wait_for_image(
        client,
        lambda image: _sample_rgb(image, exit_border_x, exit_border_y)[2] < 220,
    )
    _require_ok(client.eval_expr("_renforge_editor_end_drag()"), "visual guide drag end")
    report["guide_after_mouse_up"] = client.eval_expr(
        "_renforge_editor_guide_snapshot()"
    )


    shift_drag = _require_ok(
        client.request(
            "editor_task0_drag",
            {
                "points": [
                    [anchor_x + 5, target_center[1]],
                ],
                "shift": True,
            },
        ),
        "drag shift bypass",
    )
    report["drag_shift"] = shift_drag

    before_nudge = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 3}), "arrow repeat")
    after_three = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    _require_ok(client.request("editor_task0_key", {"key": "left", "repeat": 1, "shift": True}), "shift nudge")
    after_shift = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    report["nudge"] = {
        "before": before_nudge,
        "after_three": after_three,
        "after_shift": after_shift,
    }

    history_before = _require_ok(client.request("editor_task0_undo"), "undo")
    after_undo = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    history_after_undo = _require_ok(client.request("editor_task0_redo"), "redo")
    after_redo = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    report["history"] = {
        "undo_return": history_before,
        "undo_position": after_undo,
        "redo_return": history_after_undo,
        "redo_position": after_redo,
    }

    target_before_multi = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    top_before_multi = _bounds_for(client, "task0_top", wanted_text="OVERLAP TOP")
    top_pick = (top_before_multi["x"] + 5, top_before_multi["y"] + 5)
    _require_ok(
        client.request("editor_task0_select", {"x": top_pick[0], "y": top_pick[1]}),
        "multi target top select",
    )
    top_analysis = _wait_for_status(
        client,
        lambda status: status.get("selected_widget_id") == "task0_top"
        and bool(status.get("current_analysis_id"))
        and not status.get("selected_lock_reason"),
        timeout=6.0,
        poll_name="multi target top analysis",
    )
    _require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1, "shift": True}),
        "multi target top nudge",
    )
    top_after_multi = _bounds_for(client, "task0_top", wanted_text="OVERLAP TOP")
    current_target = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    current_target_center = _center(current_target)
    _require_ok(
        client.request(
            "editor_task0_select",
            {"x": current_target_center[0], "y": current_target_center[1]},
        ),
        "multi target reselect",
    )
    target_after_reselect = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    global_undo = _require_ok(client.request("editor_task0_undo"), "global undo")
    top_after_global_undo = _bounds_for(client, "task0_top", wanted_text="OVERLAP TOP")
    target_after_global_undo = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    global_redo = _require_ok(client.request("editor_task0_redo"), "global redo")
    top_after_global_redo = _bounds_for(client, "task0_top", wanted_text="OVERLAP TOP")
    target_after_global_redo = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    report["multi_target"] = {
        "top_analysis": top_analysis,
        "top_before": top_before_multi,
        "top_after": top_after_multi,
        "target_before": target_before_multi,
        "target_after_reselect": target_after_reselect,
        "undo": global_undo,
        "top_after_undo": top_after_global_undo,
        "target_after_undo": target_after_global_undo,
        "redo": global_redo,
        "top_after_redo": top_after_global_redo,
        "target_after_redo": target_after_global_redo,
    }

    pre_save_source = _source_snapshot(target_fixture)
    report["pre_save_source"] = pre_save_source
    pre_save_target = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    report["pre_save_target"] = pre_save_target
    save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "save",
    )
    saving_label = client.eval_expr(
        "str(getattr(renpy.get_widget('_renforge_editor_overlay', 'rf_save'), 'text', [''])[0])"
    )
    save_pending_status = _wait_for_status(
        client,
        lambda status: bool(status.get("pending_transaction_id")),
        timeout=6.0,
        poll_name="save pending",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == _source_generation(analysis_status) + 1,
        timeout=45.0,
        poll_name="save complete",
    )
    report["save_status"] = save_status
    saved_label = client.eval_expr(
        "str(getattr(renpy.get_widget('_renforge_editor_overlay', 'rf_save'), 'text', [''])[0])"
    )
    report["save_control_labels"] = {
        "saving": saving_label,
        "saved": saved_label,
    }
    report["attestation"] = {
        "ok": True,
        "state": save_status.get("status_text"),
        "script_generation": save_status.get("script_generation"),
    }
    report["save_request"] = save_request
    post_save_source = _source_snapshot(target_fixture)
    post_save_target = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    report["post_save_source"] = post_save_source
    report["post_save_target"] = post_save_target

    reset_after_save = client.request("editor_task0_reset")
    report["reset_after_save"] = reset_after_save
    successor_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and bool(status.get("current_analysis_id"))
        and not status.get("selected_lock_reason")
        and not bool(status.get("save_enabled")),
        timeout=6.0,
        poll_name="successor analysis",
    )
    report["successor_analysis"] = successor_status
    first_saved_target = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 2}), "second save nudge")
    second_pre_save_source = _source_snapshot(target_fixture)
    second_save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "second save",
    )
    second_save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == _source_generation(save_status) + 1,
        timeout=45.0,
        poll_name="second save complete",
    )
    second_successor_status = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and not status.get("selected_lock_reason"),
        timeout=6.0,
        poll_name="second successor analysis",
    )
    report["second_save"] = {
        "request": second_save_request,
        "status": second_save_status,
        "successor": second_successor_status,
        "target_before": first_saved_target,
        "target_after": _bounds_for(client, "task0_target", wanted_text="MOVE ME"),
        "source_before": second_pre_save_source,
        "source_after": _source_snapshot(target_fixture),
    }

    observed = _require_ok(client.request("editor_task0_observe_selected"), "observe selected")
    observation = observed.get("observation") or {}
    report["observation"] = observation
    screenshot_bytes = client.screenshot()
    report["observation_frame_external"] = hashlib.sha256(screenshot_bytes).hexdigest()

    queued = _require_ok(
        client.request(
            "editor_task0_coordinator_submit",
            {"observation": observation},
        ),
        "coordinator submit",
    )
    applied = None
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        collected = _require_ok(client.request("editor_task0_coordinator_collect"), "coordinator collect")
        applied_items = collected.get("applied") or []
        if applied_items:
            applied = applied_items[-1]
            break
        time.sleep(0.05)
    if applied is None:
        raise AssertionError("coordinator result never applied")
    report["coordinator"] = {
        "queued": queued,
        "applied": applied,
    }

    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity reset")
    _require_ok(client.request("editor_task0_pointer", {"x": 40, "y": 40}), "pointer far")
    far_label = client.eval_expr("_renforge_editor_label_snapshot()")
    if not isinstance(far_label, dict):
        raise AssertionError(f"attached label state unavailable: {far_label!r}")
    far_x = int(far_label["x"])
    far_y = int(far_label["y"])
    far_w = int(far_label["w"])
    far_h = int(far_label["h"])
    sample_x = far_x + 3
    sample_y = far_y + 3
    label_far = _wait_for_image(
        client,
        lambda image: sum(_sample_rgb(image, sample_x, sample_y)) < 100,
    )
    far_pixel = _sample_rgb(label_far, sample_x, sample_y)

    _require_ok(
        client.request(
            "editor_task0_pointer",
            {"x": far_x + far_w // 2, "y": far_y + far_h // 2},
        ),
        "pointer over label",
    )
    label_near = _wait_for_image(
        client,
        lambda image: sum(_sample_rgb(image, sample_x, sample_y)) > sum(far_pixel) + 20,
    )
    near_label = client.eval_expr("_renforge_editor_label_snapshot()")
    if not isinstance(near_label, dict):
        raise AssertionError(f"hovered label state unavailable: {near_label!r}")
    near_pixel = _sample_rgb(label_near, sample_x, sample_y)
    report["label"] = {
        "far_box": [far_x, far_y, far_x + far_w - 1, far_y + far_h - 1],
        "near_box": [
            int(near_label["x"]),
            int(near_label["y"]),
            int(near_label["x"]) + int(near_label["w"]) - 1,
            int(near_label["y"]) + int(near_label["h"]) - 1,
        ],
        "far_green": sum(far_pixel),
        "near_green": sum(near_pixel),
        "image_size": label_far.size,
    }

    clicks_before = int(client.get_var("renforge_editor_task0_clicks"))
    _require_ok(client.request("editor_task0_key", {"key": "escape", "repeat": 1}), "escape")
    clicked = _require_ok(client.click_element(text="MOVE ME", exact=True), "click after exit")
    clicks_after = int(client.get_var("renforge_editor_task0_clicks"))
    report["post_exit"] = {
        "click_before": clicks_before,
        "click_after": clicks_after,
        "clicked": clicked,
    }

    report["first_observation"] = first_observation
    report["frame_after"] = client.list_ui_elements_info(screen=FIXTURE_SCREEN).get("frame_id")
    return report
