from __future__ import annotations

import hashlib
import io
import shutil
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


def inject_editor_task0_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_task0.rpy"
    fixture_target = game_dir / "zz_renforge_editor_task0_fixture.rpy"
    shutil.copyfile(EDITOR_RESOURCE, editor_target)
    shutil.copyfile(FIXTURE_RESOURCE, fixture_target)
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


def run_editor_task0_live_scenario(client: Any) -> dict[str, Any]:
    report: dict[str, Any] = {}

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
    dupe_center = _center(dupe["bounds"])
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

    clipped_select = client.request("editor_task0_select", {"x": clipped_center[0], "y": clipped_center[1]})
    report["clipped_lock"] = clipped_select.get("lock_reason")

    dupe_select = client.request("editor_task0_select", {"x": dupe_center[0], "y": dupe_center[1]})
    report["dupe_lock"] = dupe_select.get("lock_reason")

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

    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity 1.0")
    guide_high = _open_png(client.screenshot())
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 0.2}), "opacity 0.2")
    guide_low = _open_png(client.screenshot())
    guide_status = client.request("editor_task0_status")
    guide_x = guide_status.get("guide_x")
    guide_y = guide_status.get("guide_y")
    sample_x = int(guide_x) if isinstance(guide_x, int) else int(guide_high.size[0] // 2)
    sample_y = int(guide_y) if isinstance(guide_y, int) else int(guide_high.size[1] // 2)
    guide_pixel_high = _sample_rgb(guide_high, sample_x, sample_y)
    guide_pixel_low = _sample_rgb(guide_low, sample_x, sample_y)
    swatch_high = _sample_rgb(guide_high, 1010, 20)
    swatch_low = _sample_rgb(guide_low, 1010, 20)

    report["guide_red"] = {
        "high": _red_score(guide_high),
        "low": _red_score(guide_low),
        "sample_high": guide_pixel_high,
        "sample_low": guide_pixel_low,
        "sample_point": [sample_x, sample_y],
        "swatch_high": swatch_high,
        "swatch_low": swatch_low,
    }
    border_rgb = _sample_rgb(guide_low, 6, 6)
    fill_rgb = _sample_rgb(guide_low, 24, 20)
    report["rf_exit_colors_low_opacity"] = {
        "border": border_rgb,
        "fill": fill_rgb,
    }

    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity reset")
    _require_ok(client.request("editor_task0_pointer", {"x": 40, "y": 40}), "pointer far")
    label_far = _open_png(client.screenshot())
    _require_ok(client.request("editor_task0_pointer", {"x": 1900, "y": 1060}), "pointer near-edge")
    label_near = _open_png(client.screenshot())
    far_box, far_green = _green_bbox_and_brightness(label_far)
    near_box, near_green = _green_bbox_and_brightness(label_near)
    report["label"] = {
        "far_box": far_box,
        "near_box": near_box,
        "far_green": far_green,
        "near_green": near_green,
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
