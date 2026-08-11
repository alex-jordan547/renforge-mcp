from __future__ import annotations

import hashlib
import io
import re
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.bridge.client import BridgeProtocolError
from renforge.editor_runner_status import is_reload_committed

FIXTURE_SCREEN = "renforge_editor_task0_fixture"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_task0_fixture.rpy"
)
SYNTHETIC_LAYOUT_SIZES = (
    (640, 480),
    (800, 600),
    (1024, 768),
    (1280, 720),
    (2560, 1080),
    (2560, 1440),
    (3440, 1440),
)


def _task0_stress_fixture_source() -> str:
    lines = [
        "",
        "screen renforge_editor_task0_stress():",
        "    layer \"screens\"",
        "    zorder -100",
        "    fixed:",
        "        xpos -10000",
        "        ypos -10000",
    ]
    indent = "        "
    for depth in range(66):
        lines.extend(
            [
                indent + "fixed:",
                indent + f'    id "task0_stress_depth_{depth}"',
                indent + "    xsize 1",
                indent + "    ysize 1",
            ]
        )
        indent += "    "
    lines.extend(
        [
            "        textbutton \"DUPE STRESS\":",
            '            id "task0_dupe_target"',
            "            xpos -10000",
            "            ypos -10000",
            "            action NullAction()",
        ]
    )
    for index in range(1001):
        lines.extend(
            [
                "        fixed:",
                f'            id "task0_stress_count_{index}"',
                "            xsize 1",
                "            ysize 1",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def inject_editor_task0_resources(
    project_root: Path,
    *,
    fixture_path: Path | None = None,
) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_task0.rpy"
    fixture_target = game_dir / "zz_renforge_editor_task0_fixture.rpy"
    stress_target = game_dir / "zz_renforge_editor_task0_stress.rpy"
    source_fixture = fixture_path or FIXTURE_RESOURCE
    shutil.copyfile(EDITOR_RESOURCE, editor_target)
    shutil.copyfile(source_fixture, fixture_target)
    stress_target.write_text(_task0_stress_fixture_source(), encoding="utf-8")
    return {
        "editor": str(editor_target),
        "fixture": str(fixture_target),
        "stress": str(stress_target),
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
    deadline = time.monotonic() + 2.0
    while True:
        try:
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
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _center(bounds: dict[str, int]) -> tuple[int, int]:
    return (int(bounds["x"] + bounds["width"] // 2), int(bounds["y"] + bounds["height"] // 2))


def _wait_for_bounds_position(
    client: Any,
    wanted_id: str,
    expected: dict[str, int],
    *,
    wanted_text: str | None = None,
    timeout: float = 2.0,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last: dict[str, int] = {}
    while True:
        last = _bounds_for(client, wanted_id, wanted_text=wanted_text)
        if last["x"] == expected["x"] and last["y"] == expected["y"]:
            return last
        if time.monotonic() >= deadline:
            raise AssertionError(f"{wanted_id!r} did not reach {expected!r}: {last!r}")
        time.sleep(0.05)


def _click_element_with_retry(
    client: Any,
    *,
    timeout: float = 2.0,
    **kwargs: Any,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        reply = client.click_element(**kwargs)
        if reply.get("ok") is True or "no UI element matching" not in str(
            reply.get("error") or ""
        ):
            return reply
        if time.monotonic() >= deadline:
            return reply
        time.sleep(0.05)


def _select_widget_with_retry(
    client: Any,
    screen_name: str,
    widget_id: str,
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        reply = client.eval_expr(
            f"_renforge_editor_select_widget('{screen_name}', '{widget_id}')"
        )
        if reply.get("ok") is True or reply.get("error") != "NO_FOCUSABLE_TARGET":
            return reply
        if time.monotonic() >= deadline:
            return reply
        time.sleep(0.05)


def _select_point_with_retry(
    client: Any,
    payload: dict[str, Any],
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        reply = client.request("editor_task0_select", payload)
        if reply.get("ok") is True or reply.get("error") != "NO_FOCUSABLE_TARGET":
            return reply
        if time.monotonic() >= deadline:
            return reply
        time.sleep(0.05)


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


def _sample_logical_rgb(
    image: Image.Image,
    x: int,
    y: int,
    *,
    logical_size: tuple[int, int] = (1280, 720),
) -> tuple[int, int, int]:
    if logical_size[0] <= 0 or logical_size[1] <= 0:
        raise ValueError("logical size must be positive")
    image_x = int(round(float(x) * float(image.width) / float(logical_size[0])))
    image_y = int(round(float(y) * float(image.height) / float(logical_size[1])))
    return _sample_rgb(image, image_x, image_y)


def _purple_border_visible(
    image: Image.Image,
    rect: list[int],
    *,
    logical_size: tuple[int, int] = (1280, 720),
) -> bool:
    """Detect the low-opacity exit affordance along its expected perimeter."""
    if len(rect) != 4 or logical_size[0] <= 0 or logical_size[1] <= 0:
        return False
    scale_x = float(image.width) / float(logical_size[0])
    scale_y = float(image.height) / float(logical_size[1])
    left = int(round(rect[0] * scale_x))
    top = int(round(rect[1] * scale_y))
    right = int(round((rect[0] + rect[2] - 1) * scale_x))
    bottom = int(round((rect[1] + rect[3] - 1) * scale_y))
    band = max(2, int(round(max(scale_x, scale_y) * 3)))
    purple_count = 0
    for y in range(max(0, top - band), min(image.height, bottom + band + 1)):
        for x in range(max(0, left - band), min(image.width, right + band + 1)):
            near_vertical = abs(x - left) <= band or abs(x - right) <= band
            near_horizontal = abs(y - top) <= band or abs(y - bottom) <= band
            if not (near_vertical or near_horizontal):
                continue
            red, green, blue = _sample_rgb(image, x, y)
            if blue >= 180 and blue >= red + 40 and blue >= green + 20:
                purple_count += 1
                if purple_count >= 4:
                    return True
    return False


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
        try:
            status = _require_ok(client.request("editor_task0_status"), poll_name)
        except BridgeProtocolError:
            time.sleep(sleep_s)
            continue
        last_status = status
        if predicate(status):
            return status
        time.sleep(sleep_s)
    raise AssertionError(f"{poll_name} timeout: {last_status!r}")


def _save_has_started(status: dict[str, Any]) -> bool:
    return (
        bool(status.get("save_in_progress"))
        and bool(status.get("save_requested"))
        and status.get("status_code") == "saving"
        and status.get("save_button_state") == "saving"
    )


def _overlay_rect(client: Any, wanted_id: str) -> list[int]:
    """Return ``[x, y, width, height]`` from the overlay's focusable UI elements."""
    elements = client.list_ui_elements(screen="_renforge_editor_overlay")
    element = _find_element(elements, wanted_id)
    bounds = element.get("bounds")
    if not isinstance(bounds, dict):
        raise AssertionError(f"element {wanted_id!r} has no bounds: {element!r}")
    return [
        int(bounds["x"]),
        int(bounds["y"]),
        int(bounds["width"]),
        int(bounds["height"]),
    ]


def _rects_overlap(left: list[int], right: list[int]) -> bool:
    return not (
        left[0] + left[2] <= right[0]
        or right[0] + right[2] <= left[0]
        or left[1] + left[3] <= right[1]
        or right[1] + right[3] <= left[1]
    )


def _verify_synthetic_layouts(client: Any) -> list[dict[str, Any]]:
    edit_ids = {
        "rf_toolbar_tool_select",
        "rf_toolbar_tool_move",
        "rf_toolbar_tool_measure",
        "rf_tools",
        "rf_opacity_down",
        "rf_opacity_up",
        "rf_undo",
        "rf_redo",
        "rf_reset",
        "rf_toolbar_layout_overlay",
        "rf_toolbar_layout_docked",
        "rf_toolbar_view_preview",
        "rf_save",
        "rf_exit",
    }
    preview_ids = {"rf_toolbar_view_edit", "rf_save", "rf_exit"}
    snapshots: list[dict[str, Any]] = []
    for width, height in SYNTHETIC_LAYOUT_SIZES:
        for view_mode, layout_mode in (
            ("edit", "overlay"),
            ("edit", "docked"),
            ("preview", "overlay"),
        ):
            reply = _require_ok(
                client.request(
                    "editor_task0_layout_snapshot",
                    {
                        "width": width,
                        "height": height,
                        "view_mode": view_mode,
                        "layout_mode": layout_mode,
                    },
                ),
                f"synthetic layout {width}x{height} {view_mode}/{layout_mode}",
            )
            metrics = reply.get("metrics") or {}
            chrome = [
                list(rect)
                for key in (
                    "toolbar_rect",
                    "hud_rect",
                    "tree_rect",
                    "inspector_rect",
                    "style_rect",
                )
                if (rect := metrics.get(key)) is not None
            ]
            for rect in chrome:
                if (
                    len(rect) != 4
                    or rect[2] <= 0
                    or rect[3] <= 0
                    or rect[0] < 0
                    or rect[1] < 0
                    or rect[0] + rect[2] > width
                    or rect[1] + rect[3] > height
                ):
                    raise AssertionError(f"synthetic chrome escapes {width}x{height}: {rect!r}")
            for index, left in enumerate(chrome):
                for right in chrome[index + 1 :]:
                    if _rects_overlap(left, right):
                        raise AssertionError(f"synthetic chrome overlaps: {left!r} / {right!r}")

            canvas = list(metrics.get("canvas_rect") or [])
            if view_mode == "preview" or layout_mode == "overlay":
                if canvas != [0, 0, width, height]:
                    raise AssertionError(f"full canvas contract failed: {metrics!r}")
            else:
                for rect in chrome:
                    if _rects_overlap(canvas, rect):
                        raise AssertionError(f"docked canvas overlaps chrome: {canvas!r} / {rect!r}")

            expected_ids = preview_ids if view_mode == "preview" else edit_ids
            if set(reply.get("toolbar_ids") or []) != expected_ids:
                raise AssertionError(f"fixed toolbar ids mismatch: {reply!r}")
            flags = [
                bool(metrics.get(name))
                for name in (
                    "show_brand",
                    "show_screen",
                    "show_lock",
                    "show_disabled_tools",
                )
            ]
            if view_mode == "preview":
                if any(flags):
                    raise AssertionError(f"preview optional toolbar content is visible: {metrics!r}")
            else:
                seen_visible = False
                for visible in flags:
                    if visible:
                        seen_visible = True
                    elif seen_visible:
                        raise AssertionError(f"toolbar elision order is invalid: {flags!r}")
            if int(metrics.get("text_size") or 0) < 12 or int(metrics.get("heading_text_size") or 0) < 14:
                raise AssertionError(f"chrome text floor is invalid: {metrics!r}")

            zoom = float(metrics.get("canvas_zoom") or 0.0)
            offset = list(metrics.get("canvas_offset") or [])
            if zoom <= 0.0 or len(offset) != 2:
                raise AssertionError(f"canvas transform is invalid: {metrics!r}")
            logical = [width / 3.0, height / 3.0]
            screen_point = [offset[0] + logical[0] * zoom, offset[1] + logical[1] * zoom]
            recovered = [
                (screen_point[0] - offset[0]) / zoom,
                (screen_point[1] - offset[1]) / zoom,
            ]
            if max(abs(recovered[i] - logical[i]) for i in (0, 1)) > 1e-6:
                raise AssertionError(f"canvas transform is not invertible: {metrics!r}")
            snapshots.append(
                {
                    "size": [width, height],
                    "view_mode": view_mode,
                    "layout_mode": layout_mode,
                    "metrics": metrics,
                }
            )
    return snapshots


def _wait_for_tree_stress(client: Any, *, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while True:
        reply = client.request(
            "eval",
            {"expr": "_renforge_editor_tree_summary()"},
            deadline=deadline,
        )
        if reply.get("error") is not None:
            raise AssertionError(f"tree stress evaluation failed: {reply!r}")
        candidate = reply.get("value")
        if isinstance(candidate, dict):
            last = candidate
            if (
                int(candidate.get("total") or 0) > 1000
                and candidate.get("count_truncated") is True
                and candidate.get("depth_truncated") is True
                and int(candidate.get("terminal_row_count") or 0) == 1
            ):
                return candidate
        if time.monotonic() >= deadline:
            raise AssertionError(
                "tree stress contract failed: "
                f"total={last.get('total')!r}, "
                f"count_truncated={last.get('count_truncated')!r}, "
                f"depth_truncated={last.get('depth_truncated')!r}"
            )
        time.sleep(0.05)


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
    report["synthetic_layouts"] = _verify_synthetic_layouts(client)

    base_elements_info = client.list_ui_elements_info(screen=FIXTURE_SCREEN)
    base_elements = base_elements_info.get("elements") or []
    target = _find_element(base_elements, "task0_target", wanted_text="MOVE ME")
    top = _find_element(base_elements, "task0_top", wanted_text="OVERLAP TOP")
    anchor = _find_element(base_elements, "task0_anchor", wanted_text="ANCHOR")
    clipped = _find_element(base_elements, "task0_clipped", wanted_text="CLIPPED")
    target_center = _center(target["bounds"])
    top_center = _center(top["bounds"])
    target_pick = (
        int(target["bounds"]["x"]) + 10,
        int(target["bounds"]["y"] + target["bounds"]["height"] - 2),
    )
    clipped_center = _center(clipped["bounds"])
    report["frame_before"] = base_elements_info.get("frame_id")
    report["target_before"] = target["bounds"]

    top_select = _require_ok(
        _select_point_with_retry(client, {"x": top_center[0], "y": top_center[1]}),
        "top select",
    )
    report["top_select_widget"] = top_select.get("selected", {}).get("widget_id")

    target_select = _require_ok(
        _select_point_with_retry(client, {"x": target_pick[0], "y": target_pick[1]}),
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
    selected_tree = client.eval_expr("_renforge_editor_tree_rows()")
    selected_rows = [row for row in selected_tree.get("rows", []) if row.get("selected")]
    if len(selected_rows) != 1 or selected_rows[0].get("screen_name") != FIXTURE_SCREEN:
        raise AssertionError(f"tree selection identity is ambiguous: {selected_rows!r}")
    report["tree_selection"] = selected_rows[0]
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
        return client.eval_expr("_renforge_editor_layout_chrome_snapshot()")

    def click_editor_control(widget_id: str, name: str) -> None:
        _require_ok(
            _click_element_with_retry(client, id=widget_id, screen="_renforge_editor_overlay"),
            name,
        )

    click_editor_control("rf_toolbar_layout_docked", "switch docked")
    docked_point = client.eval_expr(
        f"list(_renforge_editor_canvas_to_screen_point({target_center[0]}, {target_center[1]}))"
    )
    docked_select = _select_point_with_retry(
        client,
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
    origin_screen = client.eval_expr(
        "list(_renforge_editor_canvas_to_screen_point(0, 0))"
    )
    far_screen = client.eval_expr(
        "list(_renforge_editor_canvas_to_screen_point("
        "config.screen_width, config.screen_height))"
    )
    selected_rect = client.eval_expr(
        "list(_renforge_editor_state().selected_rect or [])"
    )
    marquee = None
    if isinstance(selected_rect, list) and len(selected_rect) == 4:
        tl = client.eval_expr(
            "list(_renforge_editor_canvas_to_screen_point("
            f"{int(selected_rect[0])}, {int(selected_rect[1])}))"
        )
        br = client.eval_expr(
            "list(_renforge_editor_canvas_to_screen_point("
            f"{int(selected_rect[0]) + int(selected_rect[2])}, "
            f"{int(selected_rect[1]) + int(selected_rect[3])}))"
        )
        marquee = {
            "canvas_rect": [int(v) for v in selected_rect],
            "screen_tl": [float(tl[0]), float(tl[1])],
            "screen_br": [float(br[0]), float(br[1])],
        }
        # Marquee corners must sit on the scaled canvas AABB (≤2 px).
        aabb = docked.get("canvas_aabb") or []
        if len(aabb) == 4:
            left, top, width, height = [float(v) for v in aabb]
            right, bottom = left + width, top + height
            for label, point in (("tl", marquee["screen_tl"]), ("br", marquee["screen_br"])):
                px, py = point
                if not (
                    left - 2 <= px <= right + 2 and top - 2 <= py <= bottom + 2
                ):
                    raise AssertionError(
                        f"docked marquee {label} outside canvas AABB: "
                        f"{point!r} vs {aabb!r}"
                    )
        # Round-trip: screen click centre should re-hit the same widget.
        mid_screen = [
            int(round((marquee["screen_tl"][0] + marquee["screen_br"][0]) / 2)),
            int(round((marquee["screen_tl"][1] + marquee["screen_br"][1]) / 2)),
        ]
        reselect = _select_point_with_retry(
            client,
            {
                "x": mid_screen[0],
                "y": mid_screen[1],
                "coordinate_space": "screen",
            },
        )
        if reselect.get("ok") is not True:
            raise AssertionError(
                f"docked marquee reselect failed at {mid_screen!r}: {reselect!r}"
            )
        if (reselect.get("selected") or {}).get("widget_id") != "task0_target":
            raise AssertionError(
                f"docked marquee reselect missed target: {reselect!r}"
            )
        marquee["reselect"] = {
            "point": mid_screen,
            "widget_id": (reselect.get("selected") or {}).get("widget_id"),
        }

    # Stage bands: void right of canvas must not paint game sky (black stage).
    stage_probe = client.eval_expr(
        "{"
        "'bands': _renforge_editor_dock_stage_bands(),"
        "'chrome_docked': _renforge_editor_chrome_docked(),"
        "}"
    )
    docked_png = client.screenshot()

    click_editor_control("rf_toolbar_view_preview", "switch preview")
    preview = layout_snapshot()
    # Preview must release transforms even while layout_mode stays docked.
    if preview.get("chrome_docked") is not False or int(preview.get("transforms") or 0) != 0:
        raise AssertionError(f"preview did not release dock chrome: {preview!r}")
    click_editor_control("rf_toolbar_view_edit", "restore edit")
    redocked = layout_snapshot()
    click_editor_control("rf_toolbar_layout_overlay", "restore overlay")
    report["docked_view_mode"] = {
        "selected": docked_select.get("selected", {}).get("widget_id"),
        "docked": docked,
        "preview": preview,
        "redocked": redocked,
        "overlay": layout_snapshot(),
        "canvas_origin_screen": origin_screen,
        "canvas_far_screen": far_screen,
        "marquee": marquee,
        "stage_probe": stage_probe,
        "docked_png_sha256": hashlib.sha256(docked_png).hexdigest(),
    }

    clipped_select = client.request("editor_task0_select", {"x": clipped_center[0], "y": clipped_center[1]})
    report["clipped_lock"] = clipped_select.get("lock_reason")

    validate_repeated = client.request(
        "editor_task0_validate_runtime_key",
        {
            "runtime_key": {
                "screen": FIXTURE_SCREEN,
                "invocation_path": FIXTURE_SCREEN,
                "widget_id": "task0_dupe_target",
                "source_location": ["game/zz_renforge_editor_task0_fixture.rpy", 6],
                "instance_discriminator": {
                    "kind": "use",
                    "repeated": True,
                    "instance_count": 2,
                },
                "ancestry": [],
            }
        },
    )
    report["dupe_lock"] = validate_repeated.get("lock_reason")

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

    drag_snap = _require_ok(
        client.request(
            "editor_task0_drag",
            {
                "points": [
                    [target_center[0], target_center[1]],
                    [anchor_x + 5, target_center[1]],
                    [anchor_x + 7, target_center[1]],
                    [anchor_x + 25, target_center[1]],
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
    guide_status = _wait_for_status(
        client,
        lambda status: (
            isinstance(status.get("guide_x"), int)
            or isinstance(status.get("guide_y"), int)
        ),
        timeout=2.0,
        poll_name="guide status",
    )
    report["distance_badge"] = client.eval_expr("_renforge_editor_distance_snapshot()")
    guide_snapshot = client.eval_expr("_renforge_editor_guide_snapshot()")
    if guide_snapshot.get("line_x") is None and guide_snapshot.get("line_y") is None:
        raise AssertionError(f"snap guide was not bounded: {guide_snapshot!r}")
    report["guide_snapshot"] = guide_snapshot

    opacity_before = client.screenshot()
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity 1.0")
    guide_high_png = _wait_for_screenshot_change(client, opacity_before)
    guide_high = _open_png(guide_high_png)
    # Re-read snapshot after the opacity restart so badge text cannot race a
    # mid-frame delta; Ren'Py Text stores segments as a list.
    report["distance_badge"] = client.eval_expr("_renforge_editor_distance_snapshot()")
    report["distance_badge_rendered_text"] = client.eval_expr(
        "(lambda w: ('' if w is None else ("
        "w.text if isinstance(getattr(w, 'text', None), str) else "
        "''.join(str(part) for part in (getattr(w, 'text', None) or []))"
        ")))(renpy.get_widget('_renforge_editor_overlay', 'rf_distance_x_text'))"
    )
    tools_hide_click = _require_ok(
        _click_element_with_retry(client, id="rf_tools", screen="_renforge_editor_overlay"),
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
        _click_element_with_retry(client, id="rf_tools", screen="_renforge_editor_overlay"),
        "show tools",
    )
    guide_high_png = _wait_for_screenshot_change(client, tools_hidden_png)
    guide_high = _open_png(guide_high_png)
    restored_widget = False
    restored_deadline = time.monotonic() + 2.0
    while time.monotonic() < restored_deadline:
        restored_widget = bool(
            client.eval_expr(
                "renpy.get_widget('_renforge_editor_overlay', 'rf_distance_x') is not None"
            )
        )
        if restored_widget:
            break
        time.sleep(0.05)
    report["tools_visibility"] = {
        "hide_click": tools_hide_click,
        "hidden_state": tools_hidden_state,
        "show_click": tools_show_click,
        "restored_widget": restored_widget,
    }
    # Capture the exit rect directly from the layout tree before lowering
    # opacity; the purple affordance is then sampled against this stable rect.
    exit_bounds = _overlay_rect(client, "rf_exit")
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 0.2}), "opacity 0.2")
    _wait_for_screenshot_change(client, guide_high_png)
    exit_fill_x = int(exit_bounds[0]) + 4
    exit_fill_y = int(exit_bounds[1]) + 4
    guide_low = _wait_for_image(
        client,
        lambda image: _purple_border_visible(image, exit_bounds),
    )
    if guide_snapshot.get("line_x") is not None:
        sample_x = int(guide_snapshot["line_x"][0])
        sample_y = int(guide_snapshot["line_x"][1]) + int(guide_snapshot["line_x"][2]) // 2
    else:
        sample_x = int(guide_snapshot["line_y"][0]) + int(guide_snapshot["line_y"][2]) // 2
        sample_y = int(guide_snapshot["line_y"][1])
    guide_pixel_high = _sample_logical_rgb(guide_high, sample_x, sample_y)
    guide_pixel_low = _sample_logical_rgb(guide_low, sample_x, sample_y)
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
        "border_visible": _purple_border_visible(guide_low, exit_bounds),
        "fill": _sample_logical_rgb(guide_low, exit_fill_x, exit_fill_y),
    }
    _require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity reset")
    _wait_for_image(
        client,
        lambda image: not _purple_border_visible(image, exit_bounds),
    )
    _require_ok(client.eval_expr("_renforge_editor_end_drag()"), "visual guide drag end")
    report["guide_after_mouse_up"] = client.eval_expr(
        "_renforge_editor_guide_snapshot()"
    )
    client.eval_expr("_renforge_editor_reset_history()")
    before_nudge_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    if len(before_nudge_position) != 2:
        raise AssertionError(f"nudge preview position is unavailable: {before_nudge_position!r}")
    before_nudge = {"x": int(before_nudge_position[0]), "y": int(before_nudge_position[1])}
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 1}), "arrow nudge")
    after_right_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    after_right = {"x": int(after_right_position[0]), "y": int(after_right_position[1])}
    _require_ok(client.request("editor_task0_key", {"key": "left", "repeat": 1, "shift": True}), "shift nudge")
    after_shift_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    after_shift = {"x": int(after_shift_position[0]), "y": int(after_shift_position[1])}
    collected_intents = client.eval_expr("_renforge_editor_collect_intents()")
    nudge_status = _require_ok(client.request("editor_task0_status"), "nudge status")
    if (
        len(collected_intents or []) != 1
        or int(nudge_status.get("dirty_target_count") or 0) != 1
        or int(nudge_status.get("history_length") or 0) != 2
    ):
        raise AssertionError(
            f"history intent coalescing failed: intents={collected_intents!r}, status={nudge_status!r}"
        )
    report["nudge"] = {
        "before": before_nudge,
        "after_right": after_right,
        "after_shift": after_shift,
        "intents": collected_intents,
        "status": nudge_status,
    }

    history_dimensions = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    history_before = _require_ok(client.request("editor_task0_undo"), "undo")
    undo_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    after_undo = {
        **history_dimensions,
        "x": int(undo_position[0]),
        "y": int(undo_position[1]),
    }
    history_after_undo = _require_ok(client.request("editor_task0_redo"), "redo")
    redo_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    after_redo = {
        **history_dimensions,
        "x": int(redo_position[0]),
        "y": int(redo_position[1]),
    }
    report["history"] = {
        "undo_return": history_before,
        "undo_position": after_undo,
        "redo_return": history_after_undo,
        "redo_position": after_redo,
    }

    # Step 1 fixed-toolbar proof at the authored 1280x720 resolution: every
    # functional edit action must be visible, and state changes go through the
    # rendered buttons rather than only through the request protocol.
    #
    # rf_redo is `sensitive can_redo()`, so it leaves the focus list while the
    # history sits at its head. Undo once through the protocol to make it
    # focusable before the bounds check, then exercise undo/redo via buttons.
    window_size = client.eval_expr("[config.screen_width, config.screen_height]")
    if list(window_size) != [1280, 720]:
        raise AssertionError(f"fixed toolbar proof expects a 1280x720 window: {window_size!r}")
    _require_ok(client.request("editor_task0_undo"), "undo to enable rf_redo")

    tool_replies: dict[str, Any] = {}
    for control_id, expected_mode in (
        ("rf_toolbar_tool_move", "move"),
        ("rf_toolbar_tool_measure", "measure"),
    ):
        tool_replies[control_id] = _require_ok(
            _click_element_with_retry(client, id=control_id, screen="_renforge_editor_overlay"),
            f"toolbar {expected_mode} click",
        )
        if client.eval_expr("_renforge_editor_tool_mode()") != expected_mode:
            raise AssertionError(f"toolbar did not enter {expected_mode!r} mode")
    measure_before = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    measure_key = client.request("editor_task0_key", {"key": "right", "repeat": 1})
    measure_after = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    if measure_after != measure_before:
        raise AssertionError(f"measure-mode arrows mutated the target: {measure_before!r} -> {measure_after!r}")
    tool_replies["rf_toolbar_tool_select"] = _require_ok(
        _click_element_with_retry(client, id="rf_toolbar_tool_select", screen="_renforge_editor_overlay"),
        "toolbar select click",
    )
    if client.eval_expr("_renforge_editor_tool_mode()") != "select":
        raise AssertionError("toolbar did not restore select mode")

    opacity_before_buttons = float(client.eval_expr("_renforge_editor_state().opacity"))
    opacity_down = _require_ok(
        _click_element_with_retry(client, id="rf_opacity_down", screen="_renforge_editor_overlay"),
        "toolbar opacity down click",
    )
    opacity_lowered = float(client.eval_expr("_renforge_editor_state().opacity"))
    opacity_up = _require_ok(
        _click_element_with_retry(client, id="rf_opacity_up", screen="_renforge_editor_overlay"),
        "toolbar opacity up click",
    )
    opacity_restored = float(client.eval_expr("_renforge_editor_state().opacity"))
    if not opacity_lowered < opacity_before_buttons or abs(opacity_restored - opacity_before_buttons) > 1e-6:
        raise AssertionError(
            f"toolbar opacity controls failed: {opacity_before_buttons}, {opacity_lowered}, {opacity_restored}"
        )

    def _fixed_rects() -> dict[str, Any]:
        elements = client.list_ui_elements(screen="_renforge_editor_overlay")
        collected: dict[str, Any] = {}
        for action_id in (
            "rf_toolbar_tool_select",
            "rf_toolbar_tool_move",
            "rf_toolbar_tool_measure",
            "rf_tools",
            "rf_opacity_down",
            "rf_opacity_up",
            "rf_undo",
            "rf_redo",
            "rf_reset",
            "rf_toolbar_layout_overlay",
            "rf_toolbar_layout_docked",
            "rf_toolbar_view_preview",
            "rf_save",
            "rf_exit",
        ):
            element = _find_element(elements, action_id)
            bounds = element.get("bounds")
            if not isinstance(bounds, dict):
                raise AssertionError(f"fixed action {action_id!r} has no bounds: {element!r}")
            collected[action_id] = {
                "x": int(bounds["x"]),
                "y": int(bounds["y"]),
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
            }
        return collected

    def _assert_in_window(rects: dict[str, Any]) -> None:
        for action_id, rect in rects.items():
            if (
                rect["width"] <= 0
                or rect["height"] <= 0
                or rect["x"] < 0
                or rect["y"] < 0
                or rect["x"] + rect["width"] > 1280
                or rect["y"] + rect["height"] > 720
            ):
                raise AssertionError(f"fixed action {action_id!r} escapes the window: {rect!r}")

    # rf_redo is focusable now that history is off its head. Bounds-check every
    # functional action, then click redo and undo while each is enabled.
    fixed_bounds = _fixed_rects()
    _assert_in_window(fixed_bounds)
    disabled_present: list[str] = []
    disabled_ids = (
        "rf_toolbar_tool_picker",
        "rf_toolbar_tool_text",
        "rf_toolbar_tool_hand",
    )
    show_disabled_tools = bool(
        client.eval_expr("_renforge_editor_live_layout_metrics().get('show_disabled_tools')")
    )
    for action_id in disabled_ids:
        widget_exists = bool(
            client.eval_expr(
                f"renpy.get_widget('_renforge_editor_overlay', '{action_id}') is not None"
            )
        )
        if not show_disabled_tools:
            if widget_exists:
                raise AssertionError(f"elided disabled action {action_id!r} is still rendered")
            continue
        if not widget_exists:
            raise AssertionError(f"disabled action {action_id!r} is not rendered")
        disabled_present.append(action_id)
        if client.eval_expr(
            f"bool(getattr(renpy.get_widget('_renforge_editor_overlay', '{action_id}'), 'sensitive', True))"
        ):
            raise AssertionError(f"disabled toolbar action {action_id!r} became sensitive")

    redo_click = _require_ok(
        _click_element_with_retry(client, id="rf_redo", screen="_renforge_editor_overlay"),
        "toolbar redo click",
    )
    _wait_for_status(
        client,
        lambda status: status.get("status_code") == "redo",
        timeout=5.0,
        poll_name="toolbar redo",
    )
    visible_redo_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    after_visible_redo = {
        **history_dimensions,
        "x": int(visible_redo_position[0]),
        "y": int(visible_redo_position[1]),
    }
    if after_visible_redo != after_redo:
        raise AssertionError(
            f"toolbar redo did not restore the redo position: {after_visible_redo!r} != {after_redo!r}"
        )

    undo_click = _require_ok(
        _click_element_with_retry(client, id="rf_undo", screen="_renforge_editor_overlay"),
        "toolbar undo click",
    )
    _wait_for_status(
        client,
        lambda status: status.get("status_code") == "undo",
        timeout=5.0,
        poll_name="toolbar undo",
    )
    visible_undo_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    after_visible_undo = {
        **history_dimensions,
        "x": int(visible_undo_position[0]),
        "y": int(visible_undo_position[1]),
    }
    if after_visible_undo != after_undo:
        raise AssertionError(
            f"toolbar undo did not restore the undo position: {after_visible_undo!r} != {after_undo!r}"
        )
    reset_click = _require_ok(
        _click_element_with_retry(client, id="rf_reset", screen="_renforge_editor_overlay"),
        "toolbar reset click",
    )
    _wait_for_status(
        client,
        lambda status: status.get("status_code") == "reset",
        timeout=5.0,
        poll_name="toolbar reset",
    )
    reset_restore = _require_ok(client.request("editor_task0_undo"), "undo toolbar reset")
    reset_restored = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    reset_restored_position = {
        **history_dimensions,
        "x": int(reset_restored[0]),
        "y": int(reset_restored[1]),
    }
    if reset_restored_position != after_visible_undo:
        raise AssertionError(
            "undo after toolbar reset did not restore the prior position: "
            f"{reset_restored_position!r} != {after_visible_undo!r}"
        )
    report["fixed_toolbar_actions"] = {
        "window": [1280, 720],
        "bounds": fixed_bounds,
        "disabled_present": disabled_present,
        "disabled_elided": not show_disabled_tools,
        "tool_clicks": tool_replies,
        "measure_key": measure_key,
        "opacity": {
            "before": opacity_before_buttons,
            "down": opacity_lowered,
            "restored": opacity_restored,
            "down_reply": opacity_down,
            "up_reply": opacity_up,
        },
        "undo_click": {"reply": undo_click, "target_position": after_visible_undo},
        "redo_click": {"reply": redo_click, "target_position": after_visible_redo},
        "reset_click": {
            "reply": reset_click,
            "restore_reply": reset_restore,
            "restored_position": reset_restored_position,
        },
    }

    target_before_multi = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    top_before_multi = _bounds_for(client, "task0_top", wanted_text="OVERLAP TOP")
    _require_ok(
        _select_widget_with_retry(client, FIXTURE_SCREEN, "task0_top"),
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
    top_after_position = client.eval_expr("list(_renforge_editor_state().preview_position or [])")
    top_after_multi = {
        **top_before_multi,
        "x": int(top_after_position[0]),
        "y": int(top_after_position[1]),
    }
    _require_ok(
        _select_widget_with_retry(client, FIXTURE_SCREEN, "task0_target"),
        "multi target reselect",
    )
    target_after_reselect = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    _wait_for_bounds_position(client, "task0_top", top_after_multi, wanted_text="OVERLAP TOP")
    global_undo = _require_ok(client.request("editor_task0_undo"), "global undo")
    top_after_global_undo = _wait_for_bounds_position(
        client,
        "task0_top",
        top_before_multi,
        wanted_text="OVERLAP TOP",
    )
    target_after_global_undo = _bounds_for(client, "task0_target", wanted_text="MOVE ME")
    global_redo = _require_ok(client.request("editor_task0_redo"), "global redo")
    top_after_global_redo = _wait_for_bounds_position(
        client,
        "task0_top",
        top_after_multi,
        wanted_text="OVERLAP TOP",
    )
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
        _click_element_with_retry(client, id="rf_save", screen="_renforge_editor_overlay"),
        "save",
    )
    _wait_for_status(
        client,
        _save_has_started,
        timeout=6.0,
        poll_name="save pending",
    )
    saving_label = ""
    label_deadline = time.monotonic() + 2.0
    while time.monotonic() < label_deadline:
        saving_label = client.eval_expr(
            "(lambda w: ('' if w is None else ("
            "w.text if isinstance(getattr(w, 'text', None), str) else "
            "''.join(str(part) for part in (getattr(w, 'text', None) or []))"
            ")))(renpy.get_widget('_renforge_editor_overlay', 'rf_save_text'))"
        )
        if saving_label:
            break
        time.sleep(0.05)
    if not saving_label:
        saving_label = client.eval_expr("_renforge_editor_t('save.saving')")
    save_status = _wait_for_status(
        client,
        lambda status: is_reload_committed(
            status,
            generation=_source_generation(analysis_status) + 1,
        ),
        timeout=45.0,
        poll_name="save complete",
    )
    report["save_status"] = save_status
    saved_label = client.eval_expr(
        "(lambda w: ('' if w is None else ("
        "w.text if isinstance(getattr(w, 'text', None), str) else "
        "''.join(str(part) for part in (getattr(w, 'text', None) or []))"
        ")))(renpy.get_widget('_renforge_editor_overlay', 'rf_save_text'))"
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
    report["fixed_toolbar_actions"]["reset_click"]["product_reply"] = reset_after_save
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
        _click_element_with_retry(client, id="rf_save", screen="_renforge_editor_overlay"),
        "second save",
    )
    second_save_status = _wait_for_status(
        client,
        lambda status: is_reload_committed(
            status,
            generation=_source_generation(save_status) + 1,
        ),
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
        lambda image: sum(_sample_logical_rgb(image, sample_x, sample_y)) < 180,
    )
    far_png = client.screenshot()
    label_far = _open_png(far_png)
    far_pixel = _sample_logical_rgb(label_far, sample_x, sample_y)

    near_x = far_x + far_w // 2
    near_y = far_y + far_h // 2
    near_label = client.eval_expr(
        f"(_renforge_editor_set_label({near_x}, {near_y}), "
        "_renforge_editor_label_snapshot())[1]"
    )
    if not isinstance(near_label, dict):
        raise AssertionError(f"hovered label state unavailable: {near_label!r}")
    if float(near_label.get("alpha", 1.0)) >= float(far_label.get("alpha", 0.0)):
        raise AssertionError(
            f"hovered label did not reduce alpha: {far_label!r} -> {near_label!r}"
        )
    report["label"] = {
        "far_box": [far_x, far_y, far_x + far_w - 1, far_y + far_h - 1],
        "near_box": [
            int(near_label["x"]),
            int(near_label["y"]),
            int(near_label["x"]) + int(near_label["w"]) - 1,
            int(near_label["y"]) + int(near_label["h"]) - 1,
        ],
        "far_green": sum(far_pixel),
        "far_alpha": float(far_label["alpha"]),
        "near_alpha": float(near_label["alpha"]),
        "image_size": label_far.size,
    }

    clicks_before = int(client.get_var("renforge_editor_task0_clicks"))
    exit_click = _require_ok(
        _click_element_with_retry(client, id="rf_exit", screen="_renforge_editor_overlay"),
        "toolbar exit click",
    )
    report["fixed_toolbar_actions"]["exit_click"] = exit_click
    clicked = _require_ok(
        _click_element_with_retry(client, text="MOVE ME", exact=True),
        "click after exit",
    )
    clicks_after = int(client.get_var("renforge_editor_task0_clicks"))
    report["post_exit"] = {
        "click_before": clicks_before,
        "click_after": clicks_after,
        "clicked": clicked,
    }

    report["first_observation"] = first_observation
    report["frame_after"] = client.list_ui_elements_info(screen=FIXTURE_SCREEN).get("frame_id")
    report["stress_start"] = _require_ok(
        client.request("editor_task0_start", {"screen": "renforge_editor_task0_stress"}),
        "editor_task0_start stress",
    )
    tree_stress = _wait_for_tree_stress(client)
    report["tree_stress"] = {
        "total": tree_stress.get("total"),
        "count_truncated": tree_stress.get("count_truncated"),
        "depth_truncated": tree_stress.get("depth_truncated"),
        "terminal_row_count": tree_stress.get("terminal_row_count"),
    }
    duplicate_screens = list(
        (tree_stress.get("duplicate_widget_screens") or {}).get(
            "task0_dupe_target",
            [],
        )
    )
    expected_duplicate_screens = [FIXTURE_SCREEN, "renforge_editor_task0_stress"]
    if sorted(duplicate_screens) != sorted(expected_duplicate_screens):
        raise AssertionError(f"cross-screen duplicate ids were not active: {duplicate_screens!r}")
    report["cross_screen_duplicate_ids"] = sorted(duplicate_screens)
    return report
