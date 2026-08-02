from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_textbutton_statement
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)

from renforge.editor_live_common import (
    center as _center,
    inject_editor_live_resources,
    list_ui_info,
    observe_selected as _observe_selected,
    repeated_use_lock,
    select_lock,
    sha256_file as _sha256_file,
    wait_bounds,
)

FIXTURE_SCREEN = "renforge_editor_offset_fixture"
TARGET_ID = "offset_target"

def inject_editor_offset_resources(project_root: Path) -> dict[str, str]:
    return inject_editor_live_resources(
        project_root,
        editor_basename="editor_offset",
        fixture_filename="renforge_editor_offset_fixture.rpy",
    )

def _target_line_with_offset(source_text: str) -> tuple[str, int]:
    offset = 0
    for line in source_text.splitlines(keepends=True):
        if (
            f'id "{TARGET_ID}"' in line
            and line.lstrip().startswith("textbutton ")
            and " offset " in f" {line}"
        ):
            analyze_textbutton_statement(line, expected_widget_id=TARGET_ID)
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing offset textbutton line for {TARGET_ID!r}")

def _independent_expected_after_patch(
    before_text: str,
    *,
    x: int,
    y: int,
    baseline: dict[str, int],
) -> str:
    """Build expected offset source via authored + (runtime − baseline)."""
    line, file_offset = _target_line_with_offset(before_text)
    parsed = analyze_textbutton_statement(line, expected_widget_id=TARGET_ID)
    ox = int(parsed.xpos) + (int(x) - int(baseline["x"]))
    oy = int(parsed.ypos) + (int(y) - int(baseline["y"]))
    patched_line = re.sub(
        r"(\boffset\s*\(\s*)-?\d+(\s*,\s*)-?\d+(\s*\))",
        rf"\g<1>{ox}\g<2>{oy}\3",
        line,
        count=1,
    )
    if patched_line == line:
        raise AssertionError("independent patch constructor did not change target offset pair")
    if "xpos" in patched_line or "ypos" in patched_line:
        raise AssertionError("independent constructor must not introduce xpos/ypos")
    return f"{before_text[:file_offset]}{patched_line}{before_text[file_offset + len(line) :]}"

def _outside_coordinate_spans_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text)
    after_line, _ = _target_line_with_offset(after_text)
    before_norm = re.sub(
        r"(\boffset\s*\(\s*)-?\d+(\s*,\s*)-?\d+(\s*\))",
        r"\1__X__\2__Y__\3",
        before_line,
        count=1,
    )
    after_norm = re.sub(
        r"(\boffset\s*\(\s*)-?\d+(\s*,\s*)-?\d+(\s*\))",
        r"\1__X__\2__Y__\3",
        after_line,
        count=1,
    )
    if before_norm != after_norm:
        return False
    return before_text.replace(before_line, "", 1) == after_text.replace(after_line, "", 1)

def _parse_xy_from_target_line(source_text: str) -> dict[str, int]:
    line, _ = _target_line_with_offset(source_text)
    match = re.search(r"\boffset\s*\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", line)
    if match is None:
        raise AssertionError(f"target line missing literal offset pair: {line!r}")
    return {"x": int(match.group(1)), "y": int(match.group(2))}

def run_editor_offset_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Seven-step live proof for literal offset (x, y) textbutton form (issue #41)."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = _sha256_file(fixture_path)
    baseline_text = baseline_bytes.decode("utf-8")
    # Prove analyzer accepts the authored offset form before any UI work.
    target_line, _ = _target_line_with_offset(baseline_text)
    parsed = analyze_textbutton_statement(target_line, expected_widget_id=TARGET_ID)
    if parsed.position_mode != "offset":
        raise AssertionError(f"expected position_mode offset, got {parsed.position_mode!r}")
    baseline_position = _parse_xy_from_target_line(baseline_text)
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "position": baseline_position,
        "position_mode": parsed.position_mode,
    }

    start = _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    report["start"] = start

    # Lock matrix first on real focusable widgets (measured codes).
    report["locks"] = {
        "computed": select_lock(client, "offset_computed", "OFFSET_LITERAL_REQUIRED", fixture_screen=FIXTURE_SCREEN),
        "container": select_lock(client, "offset_container", "CONTAINER_POSITION_UNSUPPORTED", fixture_screen=FIXTURE_SCREEN),
    }

    # Measured live: two use-statements share id "offset_dupe_target". The first
    # instance now resolves through the SL2 cache path and locks as
    # REPEATED_USE_UNSUPPORTED (issue #42)
    # (list_ui may only name the second instance).
    dupe_info = list_ui_info(client, FIXTURE_SCREEN)
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
            int(bounds.get("x", -1)) == 520 and int(bounds.get("y", -1)) == 430
        ):
            dupe_pick = bounds
            break
    if dupe_pick is None:
        raise AssertionError(f"could not locate first dupe bounds: {dupe_elements!r}")
    report["locks"]["ambiguous"] = repeated_use_lock(client, label="offset textbutton", bounds=dupe_pick)

    # Measured live: Side is not in the ancestry allowlist → UNKNOWN_ANCESTRY_TYPE.
    side_bounds = wait_bounds(client, "offset_side", timeout=3.0, fixture_screen=FIXTURE_SCREEN)
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
            poll_name="offset side lock",
        )
        unproven = status.get("selected_lock_reason")
    if unproven != "UNKNOWN_ANCESTRY_TYPE":
        raise AssertionError(
            f"side-ancestor offset textbutton lock was not UNKNOWN_ANCESTRY_TYPE: {unproven!r}"
        )
    report["locks"]["unproven"] = unproven

    # Step 1: resolve the editable offset textbutton from a fresh focus rect.
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
        poll_name="offset analysis",
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
        raise AssertionError(f"host did not unlock move for offset textbutton: {analysis_status!r}")

    original = analysis_status.get("selected_original_position") or analysis_status.get(
        "selected_source_position"
    )
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        original = [int(baseline_position["x"]), int(baseline_position["y"])]
    requested_before = [int(original[0]), int(original[1])]

    # Fresh focus_list observation before preview movement.
    value_baseline = client.eval_expr("renforge_editor_offset_clicks")
    before_obs = _observe_selected(client)
    before_rect = before_obs.get("rect") or []
    if len(before_rect) < 2:
        raise AssertionError(f"pre-preview observation missing rect: {before_obs!r}")
    bounds_before = [int(before_rect[0]), int(before_rect[1])]
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
        poll_name="offset preview moved",
    )
    requested_after = [
        int(preview_status["preview_position"][0]),
        int(preview_status["preview_position"][1]),
    ]
    after_obs = _observe_selected(client)
    value_preview = client.eval_expr("renforge_editor_offset_clicks")
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
        "offset save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == generation_before + 1,
        timeout=60.0,
        poll_name="offset save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_sha = _sha256_file(fixture_path)
    post_save_text = post_save_bytes.decode("utf-8")
    source_position_after = _parse_xy_from_target_line(post_save_text)
    authored_before = _parse_xy_from_target_line(pre_save_text)
    expected_source_position = {
        "x": int(authored_before["x"]) + (requested_after[0] - bounds_before[0]),
        "y": int(authored_before["y"]) + (requested_after[1] - bounds_before[1]),
    }
    if source_position_after != expected_source_position:
        raise AssertionError(
            "source offset disagrees with authored + runtime delta: "
            f"expected={expected_source_position!r}, observed={source_position_after!r}"
        )
    expected_text = _independent_expected_after_patch(
        pre_save_text,
        x=requested_after[0],
        y=requested_after[1],
        baseline={"x": bounds_before[0], "y": bounds_before[1]},
    )
    if post_save_text != expected_text:
        raise AssertionError("patched fixture bytes disagree with independent expected content")
    outside_coordinate_spans_identical = _outside_coordinate_spans_identical(
        pre_save_text,
        post_save_text,
    )
    if not outside_coordinate_spans_identical:
        raise AssertionError("source patch changed bytes outside offset spans")
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_position_after": source_position_after,
        "expected_source_position": expected_source_position,
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
        poll_name="offset post-save rebind analysis",
    )
    reload_obs = _observe_selected(client)
    value_reload = client.eval_expr("renforge_editor_offset_clicks")
    reload_rect = reload_obs.get("rect") or []
    if len(reload_rect) < 2:
        raise AssertionError(f"post-reload observation missing rect: {reload_obs!r}")
    reload_bounds = [int(reload_rect[0]), int(reload_rect[1])]
    pixel_delta = [
        reload_bounds[0] - bounds_after[0],
        reload_bounds[1] - bounds_after[1],
    ]
    if any(abs(value) > 1 for value in pixel_delta):
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
        raise AssertionError("offset textbutton undo did not restore the baseline")
    return report
