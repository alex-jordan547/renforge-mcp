"""Live V1 acceptance scenario for the editor in the demo game."""

from __future__ import annotations

import hashlib
import io
import time
from typing import Any

from PIL import Image


_EDITOR_LAUNCHER = "_renforge_editor_launcher"
_EDITOR_OVERLAY = "_renforge_editor_overlay"
_DEMO_SCREEN = "village_gate_choices"
_TAKE_ID = "demo_lantern_take"
_DECLINE_ID = "demo_lantern_decline"
_LOCKED_ID = "demo_locked_expr"


def _ed_require_ok(reply: dict[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise AssertionError(f"{name} failed: {reply!r}")
    return reply


def _ed_boot(client: Any) -> dict[str, dict[str, Any]]:
    """Reach the demo choice screen, then open RF over it.

    The story is advanced *before* the editor opens: an active editor owns every
    click, so it deliberately no longer lets `advance` play the game underneath.
    """
    launcher: dict[str, Any] = {}
    for _ in range(40):
        launcher = client.inspect_screen(_EDITOR_LAUNCHER)
        if launcher.get("active") is True:
            break
        time.sleep(0.1)
    if launcher.get("active") is not True:
        raise AssertionError(f"editor launcher never became active: {launcher!r}")

    controls: list[dict[str, Any]] = []
    for _ in range(12):
        controls = [
            control
            for control in client.list_ui_elements()
            if control.get("screen") == _DEMO_SCREEN
        ]
        found = {str(control.get("id")) for control in controls}
        if {_TAKE_ID, _DECLINE_ID, _LOCKED_ID}.issubset(found):
            break
        client.advance()
        time.sleep(0.4)
    found_by_id = {str(control.get("id")): control for control in controls}
    missing = {_TAKE_ID, _DECLINE_ID, _LOCKED_ID} - set(found_by_id)
    if missing:
        raise AssertionError(f"demo choice controls missing: {missing!r}; controls={controls!r}")

    _ed_require_ok(
        client.click_element(text="RF", exact=True, screen=_EDITOR_LAUNCHER),
        "RF launcher click",
    )
    overlay: dict[str, Any] = {}
    for _ in range(40):
        overlay = client.inspect_screen(_EDITOR_OVERLAY)
        if overlay.get("active") is True:
            break
        time.sleep(0.05)
    if overlay.get("active") is not True:
        raise AssertionError(f"editor overlay never became active: {overlay!r}")

    _ed_require_ok(
        client.request("editor_task0_start", {"screen": _DEMO_SCREEN}),
        "editor_task0_start",
    )
    return {control_id: found_by_id[control_id] for control_id in (_TAKE_ID, _DECLINE_ID, _LOCKED_ID)}


def _ed_focus_rect(client: Any, widget_id: str) -> list[int]:
    """Read one focus rectangle from the current UI frame."""
    controls = client.list_ui_elements()
    for control in controls:
        if control.get("id") != widget_id:
            continue
        bounds = control.get("bounds")
        if not isinstance(bounds, dict):
            break
        return [
            int(bounds["x"]),
            int(bounds["y"]),
            int(bounds["width"]),
            int(bounds["height"]),
        ]
    raise AssertionError(f"missing measurable focus control {widget_id!r}: {controls!r}")


def _ed_anchors(rect: list[int]) -> dict[str, list[int]]:
    """Return the left/center/right and top/center/bottom anchor lines."""
    if len(rect) != 4:
        raise AssertionError(f"focus rect must have four values: {rect!r}")
    x, y, width, height = (int(value) for value in rect)
    return {
        "x": [x, x + width // 2, x + width],
        "y": [y, y + height // 2, y + height],
    }


def _ed_select(client: Any, widget_id: str) -> dict[str, Any]:
    """Select a control at a point calculated from its observed bounds."""
    rect = _ed_focus_rect(client, widget_id)
    reply = client.request(
        "editor_task0_select",
        {"x": rect[0] + rect[2] // 2, "y": rect[1] + rect[3] // 2},
    )
    if reply.get("ok") is not True and not reply.get("lock_reason"):
        _ed_require_ok(reply, f"select {widget_id}")
    return reply


def _ed_wait_analysis(client: Any, locked: bool, *, timeout: float = 20.0) -> dict[str, Any]:
    """Wait until selected-target analysis settles, locked or editable."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _ed_require_ok(client.request("editor_task0_status"), "analysis status")
        reason = last.get("selected_lock_reason")
        if locked:
            if reason not in (None, "", "ANALYZING") and last.get("save_enabled") is False:
                return last
        elif last.get("current_analysis_id") and reason in (None, ""):
            return last
        time.sleep(0.1)
    raise AssertionError(f"analysis did not settle (locked={locked}): {last!r}")


def _ed_wait_original(client: Any, *, timeout: float = 10.0) -> list[int]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _ed_require_ok(client.request("editor_task0_status"), "original status")
        orig = last.get("selected_original_position")
        if isinstance(orig, (list, tuple)) and len(orig) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in orig):
            return [int(orig[0]), int(orig[1])]
        time.sleep(0.05)
    raise AssertionError(f"selected_original_position did not settle: {last!r}")


def _ed_wait_preview(
    client: Any,
    expect: list[int] | tuple[int, int] | None = None,
    *,
    timeout: float = 10.0,
) -> list[int]:
    """Poll until the selected widget has a concrete two-int preview."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _ed_require_ok(client.request("editor_task0_status"), "preview status")
        preview = last.get("preview_position")
        if (
            isinstance(preview, (list, tuple))
            and len(preview) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in preview)
        ):
            position = [int(preview[0]), int(preview[1])]
            if expect is None or position == [int(expect[0]), int(expect[1])]:
                return position
        time.sleep(0.05)
    raise AssertionError(f"preview did not settle: {last!r}")


def _ed_do_save(client: Any, *, timeout: float = 60.0) -> dict[str, Any]:
    """Commit the current dirty targets and wait for the reload handshake."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _ed_require_ok(client.request("editor_task0_status"), "save readiness")
        if last.get("save_enabled") is True:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"save never became enabled: {last!r}")
    _ed_require_ok(
        client.click_element(id="rf_save", screen=_EDITOR_OVERLAY),
        "editor save click",
    )
    while time.monotonic() < deadline:
        last = _ed_require_ok(client.request("editor_task0_status"), "save status")
        if not last.get("save_in_progress") and last.get("status_text") == "Reload committed":
            return last
        time.sleep(0.2)
    raise AssertionError(f"save did not commit: {last!r}")


def _open_png(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    image.load()
    return image.convert("RGB")


def _wait_for_screenshot_change(client: Any, previous: bytes, *, timeout: float = 3.0) -> bytes:
    previous_digest = hashlib.sha256(previous).digest()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = client.screenshot()
        if hashlib.sha256(current).digest() != previous_digest:
            return current
        time.sleep(0.05)
    raise AssertionError("screenshot did not change before timeout")




def _scale(client: Any, image: Image.Image) -> float:
    """Physical-pixel / logical-unit ratio (guide coords are logical)."""
    virtual = client.eval_expr("[config.screen_width, config.screen_height]")
    if isinstance(virtual, list) and len(virtual) == 2 and virtual[0] and virtual[1]:
        return float(image.size[0]) / float(virtual[0])
    return float(image.size[0]) / 1280.0


def _red_excess(
    image: Image.Image,
    y_logical: int,
    x0_logical: int,
    x1_logical: int,
    scale: float,
) -> float:
    """Mean red excess ``R - (G + B) / 2`` on one exact pixel row.

    The guide is a 1 px ``#ff3b30`` line: averaging neighbouring rows dilutes it
    below the noise floor, and a plain red average would also score the warm
    demo art. Red excess isolates the line's hue.
    """
    width, height = image.size
    pixels = image.load()
    assert pixels is not None
    yy = int(round(y_logical * scale))
    x0 = max(0, int(round(x0_logical * scale)))
    x1 = min(width, int(round(x1_logical * scale)))
    if not (0 <= yy < height) or x0 >= x1:
        return 0.0
    total = 0.0
    for xx in range(x0, x1):
        red, green, blue = pixels[xx, yy][:3]
        total += float(red) - (float(green) + float(blue)) / 2.0
    return total / (x1 - x0)


def run_demo_v1_scenario(client: Any) -> dict[str, Any]:
    """Run the complete V1 acceptance flow in one sequential Ren'Py session."""
    controls = _ed_boot(client)
    take_rect = _ed_focus_rect(client, _TAKE_ID)
    decline_rect = _ed_focus_rect(client, _DECLINE_ID)
    locked_rect = _ed_focus_rect(client, _LOCKED_ID)
    decline_anchors = _ed_anchors(decline_rect)
    decline_top = decline_anchors["y"][0]
    history_screen_before = client.inspect_screen(_DEMO_SCREEN)
    history_controls_before = [
        control
        for control in client.list_ui_elements()
        if control.get("screen") == _DEMO_SCREEN
    ]
    history_control_ids = {str(control.get("id")) for control in history_controls_before}
    history_label_before = client.get_state(state_profile="interaction").get("current_label")
    history_choice_before = client.get_var("renforge_choice")
    if (
        history_screen_before.get("active") is not True
        or not history_label_before
        or not {_TAKE_ID, _DECLINE_ID}.issubset(history_control_ids)
    ):
        raise AssertionError(
            "history screen was not ready before real drag: screen=%r label=%r controls=%r"
            % (history_screen_before, history_label_before, history_controls_before)
        )

    take_selection = _ed_require_ok(_ed_select(client, _TAKE_ID), "take selection")
    take_analysis = _ed_wait_analysis(client, locked=False)
    if take_analysis.get("selected_lock_reason") not in (None, ""):
        raise AssertionError(f"take unexpectedly locked: {take_analysis!r}")
    if not take_analysis.get("current_analysis_id"):
        raise AssertionError(f"take analysis id missing: {take_analysis!r}")
    baseline = _ed_wait_original(client)
    bx = take_rect[0] + take_rect[2] // 2
    by = take_rect[1] + take_rect[3] // 2
    real_drag_reply = _ed_require_ok(
        client.send_input(
            drag={
                "points": [[bx, by], [bx + 30, by], [bx + 60, by]],
                "button": 1,
                "coordinate_space": "logical",
            }
        ),
        "real mouse drag",
    )
    real_preview = None
    real_drag_status: dict[str, Any] = {}
    real_drag_deadline = time.monotonic() + 3.0
    while time.monotonic() < real_drag_deadline:
        real_drag_status = _ed_require_ok(
            client.request("editor_task0_status"), "real drag status"
        )
        candidate = real_drag_status.get("preview_position")
        if (
            isinstance(candidate, (list, tuple))
            and len(candidate) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in candidate)
            and list(candidate) != baseline
        ):
            real_preview = [int(candidate[0]), int(candidate[1])]
            break
        time.sleep(0.05)
    if real_preview is None:
        raise AssertionError(
            f"real mouse drag did not move preview: baseline={baseline!r}, status={real_drag_status!r}"
        )
    real_drag_delta = [
        real_preview[0] - baseline[0],
        real_preview[1] - baseline[1],
    ]
    real_moved = max(abs(real_drag_delta[0]), abs(real_drag_delta[1])) >= 20
    if not real_moved:
        raise AssertionError(
            f"real mouse drag moved less than 20px: {real_drag_delta!r}"
        )
    history_screen_after = client.inspect_screen(_DEMO_SCREEN)
    history_label_after = client.get_state(state_profile="interaction").get("current_label")
    history_choice_after = client.get_var("renforge_choice")
    screen_still_visible = history_screen_after.get("active") is True
    choice_not_activated = (
        history_label_after == history_label_before
        and history_choice_after == history_choice_before
    )
    if not screen_still_visible or not choice_not_activated:
        raise AssertionError(
            "real mouse drag propagated to story: before=(%r,%r), after=(%r,%r), screen=%r"
            % (
                history_label_before,
                history_choice_before,
                history_label_after,
                history_choice_after,
                history_screen_after,
            )
        )

    move_points = [
        [baseline[0], baseline[1]],
        [baseline[0] + 30, baseline[1]],
        [baseline[0] + 60, baseline[1]],
    ]
    drag_move = _ed_require_ok(
        client.request("editor_task0_drag", {"points": move_points, "shift": False}),
        "move drag",
    )
    moved = _ed_wait_preview(client)
    drag_delta = [moved[0] - baseline[0], moved[1] - baseline[1]]
    if abs(drag_delta[0]) < 20 or abs(drag_delta[1]) > 1:
        raise AssertionError(f"move drag did not move horizontally: {drag_delta!r}")

    cur = _ed_wait_preview(client)
    snap_points = [[cur[0], cur[1]], [cur[0], decline_top]]
    snap = _ed_require_ok(
        client.request("editor_task0_drag", {"points": snap_points, "shift": False}),
        "snap drag",
    )
    samples = snap.get("samples")
    if not isinstance(samples, list) or not samples:
        raise AssertionError(f"snap samples missing: {snap!r}")
    final_sample = samples[-1]
    guide_y = snap.get("guide_y")
    preview = final_sample.get("preview_position")
    if not isinstance(guide_y, int) or not isinstance(preview, list) or len(preview) != 2:
        raise AssertionError(f"snap did not produce a measurable guide/preview: {snap!r}")
    if abs(int(preview[1]) - decline_top) > 1 or guide_y != decline_top:
        raise AssertionError(f"snap missed decline top: {snap!r}; decline_top={decline_top}")

    opacity_before_low = client.screenshot()
    _ed_require_ok(client.request("editor_task0_set_opacity", {"opacity": 0.2}), "opacity low")
    low_png = _wait_for_screenshot_change(client, opacity_before_low)
    _ed_require_ok(client.request("editor_task0_set_opacity", {"opacity": 1.0}), "opacity high")
    high_png = _wait_for_screenshot_change(client, low_png)
    high_image = _open_png(high_png)
    low_image = _open_png(low_png)
    scale = _scale(client, high_image)
    # Sample a clear span of the guide: the selection border is painted over the
    # widget and the distance badge sits beside it, so sampling the widget's own
    # columns would measure those instead of the line.
    free_x0 = 4
    free_x1 = max(free_x0 + 16, take_rect[0] - 8)

    def guide_excess(image: Image.Image, y: int) -> float:
        return _red_excess(image, y, free_x0, free_x1, scale)

    red_high = guide_excess(high_image, guide_y)
    settle_deadline = time.monotonic() + 2.0
    while time.monotonic() < settle_deadline:
        time.sleep(0.1)
        settled_png = client.screenshot()
        settled_image = _open_png(settled_png)
        settled_red_high = guide_excess(settled_image, guide_y)
        high_png = settled_png
        high_image = settled_image
        if abs(settled_red_high - red_high) < 2:
            red_high = settled_red_high
            break
        red_high = settled_red_high
    red_low = guide_excess(low_image, guide_y)
    # Relative criteria only: the guide's own row must out-red the rows just
    # above and below it, and must fade when the overlay opacity drops.
    neighbour_high = max(
        guide_excess(high_image, guide_y - 3),
        guide_excess(high_image, guide_y + 3),
    )
    guide_row_delta = red_high - red_low
    guide_over_neighbour = red_high - neighbour_high
    guide_widget = bool(
        client.eval_expr("renpy.get_widget('_renforge_editor_overlay','rf_guide_y') is not None")
    )
    distance_y_text = client.eval_expr(
        "str(getattr(renpy.get_widget('_renforge_editor_overlay','rf_distance_y_text'), 'text', '')) "
        "if renpy.get_widget('_renforge_editor_overlay','rf_distance_y_text') is not None else ''"
    )
    if (
        not guide_widget
        or not isinstance(distance_y_text, str)
        or not distance_y_text.strip()
        or guide_over_neighbour <= 2.0
        or guide_row_delta <= 0.0
    ):
        raise AssertionError(
            "snap visual proof missing: widget=%r, text=%r, row=%.1f neighbour=%.1f "
            "low=%.1f over_neighbour=%.1f opacity_delta=%.1f"
            % (
                guide_widget,
                distance_y_text,
                red_high,
                neighbour_high,
                red_low,
                guide_over_neighbour,
                guide_row_delta,
            )
        )

    shift_drag = _ed_require_ok(
        client.request("editor_task0_drag", {"points": snap_points, "shift": True}),
        "shift drag",
    )
    if shift_drag.get("guide_x") is not None or shift_drag.get("guide_y") is not None:
        raise AssertionError(f"shift drag unexpectedly snapped: {shift_drag!r}")

    _ed_require_ok(_ed_select(client, _TAKE_ID), "take reselect for shift nudge")
    _ed_wait_analysis(client, locked=False)
    pre_shift_nudge = _ed_wait_preview(client)
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1, "shift": True}),
        "shift nudge",
    )
    post_shift_nudge = _ed_wait_preview(client)
    shift_nudge_delta = [
        post_shift_nudge[0] - pre_shift_nudge[0],
        post_shift_nudge[1] - pre_shift_nudge[1],
    ]
    if shift_nudge_delta != [10, 0]:
        raise AssertionError(f"shift nudge was not ten pixels: {shift_nudge_delta!r}")

    _ed_require_ok(_ed_select(client, _TAKE_ID), "take reselect for history")
    _ed_wait_analysis(client, locked=False)
    # The target is dirty from the drags/shift-nudge above. Reset it to its runtime
    # baseline so the history sub-scenario starts clean: Reset returns a target to
    # that same runtime baseline, so history_baseline below must equal it for the
    # reset assertion to hold in one coherent frame of reference.
    _pre_reset = client.request("editor_task0_reset", {})
    if _pre_reset.get("ok") is True:
        _ed_wait_preview(client)
        _ed_require_ok(_ed_select(client, _TAKE_ID), "take reselect after pre-reset")
        _ed_wait_analysis(client, locked=False)
    history_baseline = _ed_wait_original(client)
    before_nudge_status = _ed_require_ok(client.request("editor_task0_status"), "history before")
    before_nudge = int(before_nudge_status.get("history_length", 0))
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1}),
        "history nudge",
    )
    nudge_preview = _ed_wait_preview(client)
    after_nudge_status = _ed_require_ok(client.request("editor_task0_status"), "history after nudge")
    after_nudge = int(after_nudge_status.get("history_length", 0))
    expected_nudge_preview = [history_baseline[0] + 1, history_baseline[1]]
    if any(abs(nudge_preview[index] - expected_nudge_preview[index]) > 1 for index in range(2)):
        raise AssertionError(
            f"nudge preview mismatch: {nudge_preview!r}; expected {expected_nudge_preview!r}"
        )
    undo_reply = _ed_require_ok(client.request("editor_task0_undo"), "history undo")
    undo_preview = _ed_wait_preview(client)
    after_undo_status = _ed_require_ok(client.request("editor_task0_status"), "history after undo")
    after_undo = int(after_undo_status.get("history_length", 0))
    redo_reply = _ed_require_ok(client.request("editor_task0_redo"), "history redo")
    redo_preview = _ed_wait_preview(client)
    after_redo_status = _ed_require_ok(client.request("editor_task0_status"), "history after redo")
    after_redo = int(after_redo_status.get("history_length", 0))
    reset_reply = _ed_require_ok(client.request("editor_task0_reset"), "history reset")
    reset_preview = _ed_wait_preview(client)
    reset_status = _ed_require_ok(client.request("editor_task0_status"), "history after reset")
    after_reset = int(reset_status.get("history_length", 0))
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1}),
        "overlay history seed nudge",
    )
    overlay_seed_preview = _ed_wait_preview(client)
    if any(
        abs(overlay_seed_preview[index] - expected_nudge_preview[index]) > 1
        for index in range(2)
    ):
        raise AssertionError(
            f"overlay history seed mismatch: {overlay_seed_preview!r} vs {expected_nudge_preview!r}"
        )
    overlay_undo_reply = _ed_require_ok(
        client.click_element(id="rf_undo", screen=_EDITOR_OVERLAY),
        "overlay undo click",
    )
    overlay_undo_preview = _ed_wait_preview(client, expect=history_baseline)
    overlay_redo_reply = _ed_require_ok(
        client.click_element(id="rf_redo", screen=_EDITOR_OVERLAY),
        "overlay redo click",
    )
    overlay_redo_preview = _ed_wait_preview(client, expect=expected_nudge_preview)
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1}),
        "overlay reset seed nudge",
    )
    _ed_wait_preview(client)
    overlay_reset_reply = _ed_require_ok(
        client.click_element(id="rf_reset", screen=_EDITOR_OVERLAY),
        "overlay reset click",
    )
    overlay_reset_preview = _ed_wait_preview(client, expect=history_baseline)
    overlay_undo_ok = overlay_undo_reply.get("ok") is True
    overlay_redo_ok = overlay_redo_reply.get("ok") is True
    overlay_reset_ok = overlay_reset_reply.get("ok") is True
    if any(abs(undo_preview[index] - history_baseline[index]) > 1 for index in range(2)):
        raise AssertionError(f"undo did not restore baseline: {undo_preview!r} vs {history_baseline!r}")
    if any(abs(redo_preview[index] - expected_nudge_preview[index]) > 1 for index in range(2)):
        raise AssertionError(f"redo did not restore nudge: {redo_preview!r} vs {expected_nudge_preview!r}")
    if any(abs(reset_preview[index] - history_baseline[index]) > 1 for index in range(2)):
        raise AssertionError(f"reset did not restore baseline: {reset_preview!r} vs {history_baseline!r}")

    locked_selection = _ed_select(client, _LOCKED_ID)
    locked_status = _ed_wait_analysis(client, locked=True)
    locked_reason = locked_status.get("selected_lock_reason")
    if locked_reason in (None, "", "ANALYZING") or locked_status.get("save_enabled") is not False:
        raise AssertionError(f"locked demo control was editable: {locked_status!r}")
    lock_label_text = client.eval_expr(
        "str(_renforge_editor_label_snapshot()['text']) "
        "if _renforge_editor_label_snapshot() is not None else ''"
    )
    lock_code_in_label = (
        isinstance(lock_label_text, str)
        and bool(lock_label_text.strip())
        and str(locked_reason) in lock_label_text
    )
    if not lock_code_in_label:
        raise AssertionError(
            f"locked code missing from rendered editor label: reason={locked_reason!r}, "
            f"label={lock_label_text!r}"
        )
    locked_observation = locked_selection.get("observation")
    locked_observation_rect = (
        locked_observation.get("rect") if isinstance(locked_observation, dict) else None
    )
    if not isinstance(locked_observation_rect, list) or len(locked_observation_rect) != 4:
        raise AssertionError(f"locked observation is not measurable: {locked_selection!r}")

    _ed_require_ok(_ed_select(client, _TAKE_ID), "take reselect for multi")
    _ed_wait_analysis(client, locked=False)
    _ed_wait_original(client)
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1}),
        "multi take nudge",
    )
    _ed_require_ok(_ed_select(client, _DECLINE_ID), "decline selection for multi")
    _ed_wait_analysis(client, locked=False)
    _ed_wait_original(client)
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1}),
        "multi decline nudge",
    )
    multi_before_undo = _ed_require_ok(client.request("editor_task0_status"), "multi dirty status")
    dirty_before_undo = int(multi_before_undo.get("dirty_target_count", 0))
    if dirty_before_undo < 2:
        raise AssertionError(f"multi-target dirty count did not reach two: {multi_before_undo!r}")
    _ed_require_ok(client.request("editor_task0_undo"), "multi undo")
    deadline = time.monotonic() + 10.0
    multi_after_undo: dict[str, Any] = {}
    while time.monotonic() < deadline:
        multi_after_undo = _ed_require_ok(client.request("editor_task0_status"), "multi undo status")
        if int(multi_after_undo.get("dirty_target_count", 0)) <= 1:
            break
        time.sleep(0.05)
    dirty_after_undo = int(multi_after_undo.get("dirty_target_count", 0))
    if dirty_after_undo > 1:
        raise AssertionError(f"multi-target undo left too many dirty targets: {multi_after_undo!r}")

    save1 = _ed_do_save(client)
    gen1 = int(save1.get("script_generation", 0))
    reset_after_save = client.request("editor_task0_reset")
    if reset_after_save.get("error") != "RESET_UNAVAILABLE":
        raise AssertionError(f"reset after save was unexpectedly available: {reset_after_save!r}")

    _ed_require_ok(_ed_select(client, _TAKE_ID), "take reselect for second save")
    _ed_wait_analysis(client, locked=False)
    _ed_wait_original(client)
    _ed_require_ok(
        client.request("editor_task0_key", {"key": "right", "repeat": 1}),
        "second save nudge",
    )
    _ed_wait_analysis(client, locked=False)
    save2 = _ed_do_save(client)
    gen2 = int(save2.get("script_generation", 0))
    if gen2 <= gen1:
        raise AssertionError(f"script generation did not advance: {gen1}->{gen2}")

    return {
        "controls": controls,
        "take_rect": take_rect,
        "decline_rect": decline_rect,
        "locked_rect": locked_rect,
        "decline_top": decline_top,
        "take_selection": take_selection,
        "take_analysis": take_analysis,
        "baseline": baseline,
        "undo_preview": undo_preview,
        "redo_preview": redo_preview,
        "reset_preview": reset_preview,
        "reset_ok": reset_reply.get("ok") is True,
        "drag_move": drag_move,
        "real_mouse_drag": {
            "reply": real_drag_reply,
            "baseline": baseline,
            "preview": real_preview,
            "delta": real_drag_delta,
            "moved": real_moved,
            "screen_before": history_screen_before,
            "controls_before": history_controls_before,
            "screen_after": history_screen_after,
            "screen_still_visible": screen_still_visible,
            "choice_before": history_choice_before,
            "choice_after": history_choice_after,
            "choice_not_activated": choice_not_activated,
            "label_before": history_label_before,
            "label_after": history_label_after,
        },
        "drag_delta": drag_delta,
        "snap": {
            "reply": snap,
            "guide_y": guide_y,
            "preview": preview,
            "preview_on_anchor": abs(int(preview[1]) - decline_top) <= 1,
            "guide_widget": guide_widget,
            "distance_y_text": distance_y_text,
            "guide_row_delta": guide_row_delta,
            "guide_over_neighbour": guide_over_neighbour,
            "low_png_bytes": len(low_png),
        },
        "shift_drag": shift_drag,
        "shift_nudge": {
            "before": pre_shift_nudge,
            "after": post_shift_nudge,
            "delta": shift_nudge_delta,
        },
        "history": {
            "baseline": history_baseline,
            "before_nudge": before_nudge,
            "after_nudge": after_nudge,
            "nudge_preview": nudge_preview,
            "undo": undo_reply,
            "undo_preview": undo_preview,
            "after_undo": after_undo,
            "redo": redo_reply,
            "redo_preview": redo_preview,
            "after_redo": after_redo,
            "reset": reset_reply,
            "reset_ok": reset_reply.get("ok") is True,
            "reset_preview": reset_preview,
            "after_reset": after_reset,
        },
        "overlay_undo_ok": overlay_undo_ok,
        "overlay_redo_ok": overlay_redo_ok,
        "overlay_reset_ok": overlay_reset_ok,
        "locked": {
            "selection": locked_selection,
            "selected_lock_reason": locked_reason,
            "save_enabled": locked_status.get("save_enabled"),
            "rect": locked_rect,
            "observation_rect": locked_observation_rect,
            "observation": locked_observation,
            "lock_label_text": lock_label_text,
            "lock_code_in_label": lock_code_in_label,
        },
        "multi": {
            "dirty_before_undo": dirty_before_undo,
            "dirty_after_undo": dirty_after_undo,
            "status_before_undo": multi_before_undo,
            "status_after_undo": multi_after_undo,
        },
        "save1": save1,
        "gen1": gen1,
        "reset_after_save_error": reset_after_save.get("error"),
        "reset_after_save": reset_after_save,
        "save2": save2,
        "gen2": gen2,
    }
