from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_textbutton_statement, pixels_to_align, _format_align_component
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)

FIXTURE_SCREEN = "renforge_editor_align_fixture"
TARGET_ID = "align_target"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_align_fixture.rpy"
)


def inject_editor_align_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_align.rpy"
    fixture_target = game_dir / "zz_renforge_editor_align_fixture.rpy"
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
        if wanted_text is not None:
            if element_id == wanted_id and str(element.get("text") or "") == wanted_text:
                return element
            continue
        if element_id == wanted_id:
            return element
    raise AssertionError(
        f"missing expected element id {wanted_id!r} text {wanted_text!r}: {elements!r}"
    )


def _center(bounds: dict[str, Any]) -> tuple[int, int]:
    return (
        int(bounds["x"]) + int(bounds["width"]) // 2,
        int(bounds["y"]) + int(bounds["height"]) // 2,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _list_info(client: Any) -> dict[str, Any]:
    info = client.list_ui_elements_info(screen=FIXTURE_SCREEN)
    if not isinstance(info, dict):
        raise AssertionError(f"list_ui_elements_info returned non-dict: {info!r}")
    return info


def _wait_bounds(client: Any, widget_id: str, *, timeout: float = 6.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        info = _list_info(client)
        last = info.get("elements") if isinstance(info.get("elements"), list) else []
        try:
            element = _find_element(last, widget_id)
        except AssertionError:
            time.sleep(0.05)
            continue
        bounds = element.get("bounds")
        if isinstance(bounds, dict):
            return {
                "x": int(bounds["x"]),
                "y": int(bounds["y"]),
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
            }
        time.sleep(0.05)
    raise AssertionError(f"bounds for {widget_id!r} unavailable: {last!r}")


def _target_line_with_offset(source_text: str) -> tuple[str, int]:
    offset = 0
    for line in source_text.splitlines(keepends=True):
        if (
            f'id "{TARGET_ID}"' in line
            and line.lstrip().startswith("textbutton ")
            and " align " in f" {line}"
        ):
            analyze_textbutton_statement(line, expected_widget_id=TARGET_ID)
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing align textbutton line for {TARGET_ID!r}")


def _independent_expected_after_patch(
    before_text: str,
    *,
    x: int,
    y: int,
    baseline: dict[str, int] | None = None,
) -> str:
    """Build expected align source without calling apply_textbutton_patch."""
    line, offset = _target_line_with_offset(before_text)
    parsed = analyze_textbutton_statement(line, expected_widget_id=TARGET_ID)
    from renforge.editor.source import _format_align_component
    parent_w, parent_h = parsed.align_parent_size
    base = baseline or {"x": 0, "y": 0, "w": 0, "h": 0}
    widget_w = int(base.get("w") or 0)
    widget_h = int(base.get("h") or 0)
    # Signed extents; zero-extent axes keep authored fractions (no clamp-to-1).
    extent_w = int(parent_w) - widget_w
    extent_h = int(parent_h) - widget_h
    dx = int(x) - int(base["x"])
    dy = int(y) - int(base["y"])
    if extent_w == 0:
        if dx != 0:
            raise AssertionError("independent constructor: zero X extent with non-zero delta")
        ax = float(parsed.xpos)
    else:
        ax = float(parsed.xpos) + dx / float(extent_w)
    if extent_h == 0:
        if dy != 0:
            raise AssertionError("independent constructor: zero Y extent with non-zero delta")
        ay = float(parsed.ypos)
    else:
        ay = float(parsed.ypos) + dy / float(extent_h)
    ax_t = _format_align_component(ax)
    ay_t = _format_align_component(ay)
    patched_line = re.sub(
        r"(\balign\s*\(\s*)[^,]+(\s*,\s*)[^)]+(\s*\))",
        rf"\g<1>{ax_t}\g<2>{ay_t}\3",
        line,
        count=1,
    )
    if patched_line == line:
        raise AssertionError("independent patch constructor did not change target align pair")
    if "xpos" in patched_line or "ypos" in patched_line:
        raise AssertionError("independent constructor must not introduce xpos/ypos")
    return f"{before_text[:offset]}{patched_line}{before_text[offset + len(line) :]}"


def _outside_coordinate_spans_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text)
    after_line, _ = _target_line_with_offset(after_text)
    before_norm = re.sub(
        r"(\balign\s*\(\s*)[^,]+(\s*,\s*)[^)]+(\s*\))",
        r"\1__X__\2__Y__\3",
        before_line,
        count=1,
    )
    after_norm = re.sub(
        r"(\balign\s*\(\s*)[^,]+(\s*,\s*)[^)]+(\s*\))",
        r"\1__X__\2__Y__\3",
        after_line,
        count=1,
    )
    if before_norm != after_norm:
        return False
    return before_text.replace(before_line, "", 1) == after_text.replace(after_line, "", 1)


def _parse_xy_from_target_line(source_text: str, *, widget_size: tuple[int, int]) -> dict[str, int]:
    """Return pixel position derived from authored align via the conversion contract."""
    line, _ = _target_line_with_offset(source_text)
    parsed = analyze_textbutton_statement(line, expected_widget_id=TARGET_ID)
    if parsed.position_mode != "align":
        raise AssertionError(f"expected align mode: {parsed!r}")
    from renforge.editor.source import align_to_pixels
    px, py = align_to_pixels(
        float(parsed.xpos),
        float(parsed.ypos),
        widget_size=widget_size,
    )
    return {"x": px, "y": py}


def _select_lock(client: Any, widget_id: str, expected_code: str) -> str:
    bounds = _wait_bounds(client, widget_id)
    selection = client.request(
        "editor_task0_select",
        {"x": _center(bounds)[0], "y": _center(bounds)[1]},
    )
    immediate = selection.get("lock_reason") if isinstance(selection, dict) else None
    if immediate == expected_code:
        return expected_code
    status = _wait_for_status(
        client,
        lambda current: current.get("selected_widget_id") == widget_id
        and current.get("selected_lock_reason") == expected_code,
        timeout=10.0,
        poll_name=f"{widget_id} lock",
    )
    lock_reason = status.get("selected_lock_reason")
    if lock_reason != expected_code:
        raise AssertionError(f"unexpected lock for {widget_id!r}: {status!r}")
    return str(lock_reason)


def _observe_selected(client: Any) -> dict[str, Any]:
    reply = client.request("editor_task0_observe_selected", {})
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise AssertionError(f"observe_selected failed: {reply!r}")
    observation = reply.get("observation")
    if not isinstance(observation, dict):
        raise AssertionError(f"observe_selected missing observation: {reply!r}")
    return observation


def run_editor_align_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Seven-step live proof for literal align (x, y) textbutton form (issue #39)."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = _sha256_file(fixture_path)
    baseline_text = baseline_bytes.decode("utf-8")
    # Prove analyzer accepts the authored align form before any UI work.
    target_line, _ = _target_line_with_offset(baseline_text)
    parsed = analyze_textbutton_statement(target_line, expected_widget_id=TARGET_ID)
    if parsed.position_mode != "align":
        raise AssertionError(f"expected position_mode align, got {parsed.position_mode!r}")
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "authored_align": [float(parsed.xpos), float(parsed.ypos)],
        "position_mode": parsed.position_mode,
    }

    start = _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    report["start"] = start

    # Lock matrix first on real focusable widgets (measured codes).
    report["locks"] = {
        "computed": _select_lock(client, "align_computed", "ALIGN_LITERAL_REQUIRED"),
        "container": _select_lock(client, "align_container", "CONTAINER_POSITION_UNSUPPORTED"),
    }

    # Measured live: two use-statements share id "align_dupe_target". The first
    # instance is still selectable by focus bounds and locks as SYNTHETIC_WIDGET_ID
    # (list_ui may only name the second instance).
    dupe_info = _list_info(client)
    dupe_elements = dupe_info.get("elements") if isinstance(dupe_info, dict) else None
    # Prefer the unnamed first instance (NullAction-ish id) then fall back to
    # the named target bounds at the left dupe coordinate.
    dupe_pick = None
    for element in dupe_elements if isinstance(dupe_elements, list) else []:
        bounds = element.get("bounds")
        if not isinstance(bounds, dict):
            continue
        text = str(element.get("text") or "")
        if text == "DUPE A" or (
            str(element.get("text") or "") == "DUPE A"
        ):
            dupe_pick = bounds
            break
    if dupe_pick is None:
        raise AssertionError(f"could not locate first dupe bounds: {dupe_elements!r}")
    dupe_select = client.request(
        "editor_task0_select",
        {
            "x": int(dupe_pick["x"]) + max(2, int(dupe_pick["width"]) // 4),
            "y": int(dupe_pick["y"]) + int(dupe_pick["height"]) // 2,
        },
    )
    ambiguous = dupe_select.get("lock_reason") if isinstance(dupe_select, dict) else None
    if ambiguous in (None, ""):
        status = _wait_for_status(
            client,
            lambda current: current.get("selected_lock_reason") == "SYNTHETIC_WIDGET_ID",
            timeout=10.0,
            poll_name="pos dupe lock",
        )
        ambiguous = status.get("selected_lock_reason")
    if ambiguous != "SYNTHETIC_WIDGET_ID":
        raise AssertionError(
            f"duplicate align textbutton lock was not SYNTHETIC_WIDGET_ID: {ambiguous!r}"
        )
    report["locks"]["ambiguous"] = ambiguous

    # Measured live: Side is not in the ancestry allowlist → UNKNOWN_ANCESTRY_TYPE.
    side_bounds = _wait_bounds(client, "align_side", timeout=3.0)
    side_select = client.request(
        "editor_task0_select",
        {"x": _center(side_bounds)[0], "y": _center(side_bounds)[1]},
    )
    unproven = side_select.get("lock_reason") if isinstance(side_select, dict) else None
    if unproven in (None, ""):
        status = _wait_for_status(
            client,
            lambda current: current.get("selected_lock_reason") == "UNKNOWN_ANCESTRY_TYPE",
            timeout=10.0,
            poll_name="pos side lock",
        )
        unproven = status.get("selected_lock_reason")
    if unproven != "UNKNOWN_ANCESTRY_TYPE":
        raise AssertionError(
            f"side-ancestor align textbutton lock was not UNKNOWN_ANCESTRY_TYPE: {unproven!r}"
        )
    report["locks"]["unproven"] = unproven

    # Step 1: resolve the editable align textbutton from a fresh focus rect.
    target_bounds = _wait_bounds(client, TARGET_ID)
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
        poll_name="pos analysis",
    )
    source_key = analysis_status.get("current_source_key") or {}
    capabilities = analysis_status.get("current_capabilities") or {}
    host_move = capabilities.get("move")
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "lock_reason": analysis_status.get("selected_lock_reason"),
        "move": host_move,
        "analysis_id": analysis_status.get("current_analysis_id"),
        "measurement_method": observation.get("measurement_method"),
        "frame_id": observation.get("frame_id"),
        "source_key": source_key,
    }
    if source_key.get("statement_kind") != "textbutton":
        raise AssertionError(f"expected textbutton statement_kind: {source_key!r}")
    if host_move is not True:
        raise AssertionError(f"host did not unlock move for align textbutton: {analysis_status!r}")

    original = analysis_status.get("selected_original_position") or analysis_status.get(
        "selected_source_position"
    )
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        raise AssertionError(f"missing selected original position: {analysis_status!r}")
    requested_before = [int(original[0]), int(original[1])]

    # Fresh focus_list observation before preview movement.
    value_baseline = client.eval_expr("renforge_editor_align_clicks")
    before_obs = _observe_selected(client)
    before_rect = before_obs.get("rect") or []
    if len(before_rect) < 4:
        raise AssertionError(f"pre-preview observation missing full rect: {before_obs!r}")
    bounds_before = [int(before_rect[0]), int(before_rect[1])]
    widget_size = (int(before_rect[2]), int(before_rect[3]))
    if widget_size[0] <= 0 or widget_size[1] <= 0:
        raise AssertionError(f"pre-preview widget size unusable: {before_rect!r}")
    # Contract check: authored align + measured size must match measured TL.
    expected_tl = _parse_xy_from_target_line(baseline_text, widget_size=widget_size)
    if abs(expected_tl["x"] - bounds_before[0]) > 1 or abs(expected_tl["y"] - bounds_before[1]) > 1:
        raise AssertionError(
            "authored align conversion disagrees with measured focus TL: "
            f"expected={expected_tl!r}, measured={bounds_before!r}, widget={widget_size!r}"
        )
    report["fixture_before"]["position"] = expected_tl
    report["fixture_before"]["widget_size"] = list(widget_size)
    frame_before = before_obs.get("frame_id")

    # Step 2: preview.
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 24}), "nudge right")
    _require_ok(client.request("editor_task0_key", {"key": "down", "repeat": 16}), "nudge down")
    preview_status = _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_position"), (list, tuple))
        and len(status.get("preview_position") or []) == 2
        and list(status.get("preview_position") or []) != requested_before,
        timeout=8.0,
        poll_name="pos preview moved",
    )
    requested_after = [
        int(preview_status["preview_position"][0]),
        int(preview_status["preview_position"][1]),
    ]
    after_obs = _observe_selected(client)
    value_preview = client.eval_expr("renforge_editor_align_clicks")
    after_rect = after_obs.get("rect") or []
    if len(after_rect) < 2:
        raise AssertionError(f"post-preview observation missing rect: {after_obs!r}")
    bounds_after = [int(after_rect[0]), int(after_rect[1])]
    frame_after = after_obs.get("frame_id")
    if not frame_before or not frame_after or frame_before == frame_after:
        raise AssertionError(
            f"preview observations must have distinct real frame_ids: "
            f"before={frame_before!r}, after={frame_after!r}"
        )
    if before_obs.get("measurement_method") != "focus_list" or after_obs.get("measurement_method") != "focus_list":
        raise AssertionError(
            "preview observations must report measurement_method from bridge: "
            f"before={before_obs.get('measurement_method')!r}, after={after_obs.get('measurement_method')!r}"
        )
    requested_delta = [requested_after[axis] - requested_before[axis] for axis in (0, 1)]
    observed_delta = [bounds_after[axis] - bounds_before[axis] for axis in (0, 1)]
    # Align previews use absolute xpos overrides; allow 1px focus noise.
    if any(abs(observed_delta[axis] - requested_delta[axis]) > 1 for axis in (0, 1)):
        raise AssertionError(
            "focus_list preview bounds disagree with requested movement: "
            f"requested={requested_delta!r}, observed={observed_delta!r}"
        )
    report["preview"] = {
        "bounds_before": bounds_before,
        "bounds_after": bounds_after,
        "requested_before": requested_before,
        "requested_after": requested_after,
        "requested_delta": requested_delta,
        "observed_delta": observed_delta,
        "measurement_method_before": before_obs.get("measurement_method"),
        "measurement_method_after": after_obs.get("measurement_method"),
        "measurement_method": after_obs.get("measurement_method"),
        "frame_id_before": frame_before,
        "frame_id_after": frame_after,
    }

    # Step 3: patch via real Save overlay control.
    pre_save_bytes = fixture_path.read_bytes()
    pre_save_sha = _sha256_file(fixture_path)
    pre_save_text = pre_save_bytes.decode("utf-8")
    generation_before = _source_generation(analysis_status)
    save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "pos save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == generation_before + 1,
        timeout=60.0,
        poll_name="pos save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_sha = _sha256_file(fixture_path)
    post_save_text = post_save_bytes.decode("utf-8")
    # Align sources store fractions; verify target line form + independent bytes.
    post_target_line, _ = _target_line_with_offset(post_save_text)
    if "align (" not in post_target_line or " xpos " in f" {post_target_line}" or " ypos " in f" {post_target_line}":
        raise AssertionError(f"post-save target lost align form: {post_target_line!r}")
    expected_text = _independent_expected_after_patch(
        pre_save_text,
        x=requested_after[0],
        y=requested_after[1],
        baseline={"x": bounds_before[0], "y": bounds_before[1], "w": before_rect[2] if len(before_rect)>2 else 0, "h": before_rect[3] if len(before_rect)>3 else 0},
    )
    if post_save_text != expected_text:
        raise AssertionError("patched fixture bytes disagree with independent expected content")
    outside_coordinate_spans_identical = _outside_coordinate_spans_identical(
        pre_save_text,
        post_save_text,
    )
    if not outside_coordinate_spans_identical:
        raise AssertionError("source patch changed bytes outside xpos/ypos spans")
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_position_after": {"x": requested_after[0], "y": requested_after[1]},
        "outside_coordinate_spans_identical": outside_coordinate_spans_identical,
        "matches_independent_expected": True,
        "save_request": save_request,
    }

    # Step 4: reload publication cycle (frame_id filled after rebind observation).
    report["reload"] = {
        "ok": True,
        "script_generation": _source_generation(save_status),
        "status_text": save_status.get("status_text"),
        "generation_delta": _source_generation(save_status) - generation_before,
        "pending_handshake_sent": save_status.get("pending_handshake_sent"),
        "frame_id": None,
    }

    # Step 5: pixel agreement between post-preview focus rect and post-reload focus rect.
    successor = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="pos post-save rebind analysis",
    )
    reload_obs = _observe_selected(client)
    value_reload = client.eval_expr("renforge_editor_align_clicks")
    reload_rect = reload_obs.get("rect") or []
    if len(reload_rect) < 2:
        raise AssertionError(f"post-reload observation missing rect: {reload_obs!r}")
    reload_bounds = [int(reload_rect[0]), int(reload_rect[1])]
    pixel_delta = [
        reload_bounds[0] - bounds_after[0],
        reload_bounds[1] - bounds_after[1],
    ]
    if any(abs(value) > 1 for value in pixel_delta):  # issue #39: within one logical pixel
        raise AssertionError(
            "post-reload focus rect disagrees with post-preview focus rect: "
            f"preview={bounds_after!r}, reload={reload_bounds!r}, delta={pixel_delta!r}"
        )
    report["pixel_agreement"] = {
        "preview_after": bounds_after,
        "reload_after": reload_bounds,
        "delta": pixel_delta,
        "measurement_method": reload_obs.get("measurement_method"),
        "measurement_method_preview": after_obs.get("measurement_method"),
        "measurement_method_reload": reload_obs.get("measurement_method"),
        "frame_id_preview": frame_after,
        "frame_id_reload": reload_obs.get("frame_id"),
    }
    reload_frame = reload_obs.get("frame_id")
    if not reload_frame or reload_frame == frame_after:
        raise AssertionError(
            f"reload observation must have a distinct real frame_id: "
            f"preview={frame_after!r}, reload={reload_frame!r}"
        )
    report["value_invariance"] = {
        "baseline": value_baseline,
        "preview": value_preview,
        "reload": value_reload,
    }
    if value_preview != value_baseline or value_reload != value_baseline:
        raise AssertionError(
            "textbutton click counter changed during preview/reload: "
            f"baseline={value_baseline!r}, preview={value_preview!r}, reload={value_reload!r}"
        )
    report["reload"]["frame_id"] = reload_obs.get("frame_id")

    # Step 6: rebinding.
    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id")
        not in (None, analysis_status.get("current_analysis_id"))
        and (successor.get("current_source_key") or {}).get("statement_kind") == "textbutton",
        "widget_id": successor.get("selected_widget_id"),
        "analysis_id": successor.get("current_analysis_id"),
        "previous_analysis_id": analysis_status.get("current_analysis_id"),
        "source_key": successor.get("current_source_key"),
        "lock_reason": successor.get("selected_lock_reason"),
    }
    if report["rebinding"]["ok"] is not True:
        raise AssertionError(f"rebinding failed: {report['rebinding']!r}")

    # Step 7: byte-identical undo of the temporary fixture.
    fixture_path.write_bytes(baseline_bytes)
    restored_bytes = fixture_path.read_bytes()
    restored_sha = _sha256_file(fixture_path)
    report["byte_identical_undo"] = {
        "baseline_sha256": baseline_sha,
        "restored_sha256": restored_sha,
        "matches_baseline": restored_sha == baseline_sha and restored_bytes == baseline_bytes,
        "patched_differed": post_save_sha != baseline_sha and post_save_bytes != baseline_bytes,
    }
    if restored_bytes != baseline_bytes:
        raise AssertionError("align textbutton undo did not restore the baseline")
    return report
