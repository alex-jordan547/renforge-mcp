from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_vbar_statement
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)
from renforge.editor_live_common import repeated_use_lock

FIXTURE_SCREEN = "renforge_editor_vbar_fixture"
TARGET_ID = "vbar_target"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_vbar_fixture.rpy"
)


def inject_editor_vbar_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_vbar.rpy"
    fixture_target = game_dir / "zz_renforge_editor_vbar_fixture.rpy"
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
        if f'id "{TARGET_ID}"' in line and line.lstrip().startswith("vbar "):
            analyze_vbar_statement(line, expected_widget_id=TARGET_ID)
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing target line for {TARGET_ID!r}")


def _independent_expected_after_patch(before_text: str, *, x: int, y: int) -> str:
    """Build expected fixture text without calling apply_vbar_patch."""
    line, offset = _target_line_with_offset(before_text)
    patched_line = re.sub(r"(\bxpos\s+)-?\d+", rf"\g<1>{int(x)}", line, count=1)
    patched_line = re.sub(r"(\bypos\s+)-?\d+", rf"\g<1>{int(y)}", patched_line, count=1)
    if patched_line == line:
        raise AssertionError("independent patch constructor did not change target line")
    return f"{before_text[:offset]}{patched_line}{before_text[offset + len(line) :]}"


def _outside_coordinate_spans_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text)
    after_line, _ = _target_line_with_offset(after_text)
    before_norm = re.sub(r"(\bxpos\s+)-?\d+", r"\1__X__", before_line, count=1)
    before_norm = re.sub(r"(\bypos\s+)-?\d+", r"\1__Y__", before_norm, count=1)
    after_norm = re.sub(r"(\bxpos\s+)-?\d+", r"\1__X__", after_line, count=1)
    after_norm = re.sub(r"(\bypos\s+)-?\d+", r"\1__Y__", after_norm, count=1)
    if before_norm != after_norm:
        return False
    before_rest = before_text.replace(before_line, "", 1)
    after_rest = after_text.replace(after_line, "", 1)
    return before_rest == after_rest


def _parse_xy_from_target_line(source_text: str) -> dict[str, int]:
    line, _ = _target_line_with_offset(source_text)
    xpos = re.search(r"\bxpos\s+(-?\d+)\b", line)
    ypos = re.search(r"\bypos\s+(-?\d+)\b", line)
    if xpos is None or ypos is None:
        raise AssertionError(f"target line missing literal xpos/ypos: {line!r}")
    return {"x": int(xpos.group(1)), "y": int(ypos.group(1))}


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


def run_editor_vbar_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Seven-step live proof for the dedicated single-line vbar adapter."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
    baseline_text = baseline_bytes.decode("utf-8")
    baseline_position = _parse_xy_from_target_line(baseline_text)
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "position": baseline_position,
    }

    start = _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    report["start"] = start

    # Lock matrix first on real focusable widgets (measured codes).
    report["locks"] = {
        "computed": _select_lock(client, "vbar_computed", "XPOS_LITERAL_REQUIRED"),
        "style": _select_lock(client, "vbar_style", "BAR_STYLE_POSITION_UNSUPPORTED"),
        "missing_position": _select_lock(
            client, "vbar_missing_position", "BAR_POSITION_NOT_DIRECTLY_AUTHORED"
        ),
        "container": _select_lock(client, "vbar_container", "CONTAINER_POSITION_UNSUPPORTED"),
    }

    # Measured live: two use-statements with the same id surface as
    # vbar_dupe_target / vbar_dupe_target#2 and resolve with REPEATED_USE_UNSUPPORTED.
    dupe_info = _list_info(client)
    dupe_elements = dupe_info.get("elements") if isinstance(dupe_info, dict) else None
    dupe_candidates = [
        element
        for element in (dupe_elements if isinstance(dupe_elements, list) else [])
        if str(element.get("id") or "").startswith("vbar_dupe_target")
    ]
    if len(dupe_candidates) < 2:
        raise AssertionError(f"expected two vbar_dupe_target instances: {dupe_candidates!r}")
    dupe = next(
        (element for element in dupe_candidates if str(element.get("id") or "") == "vbar_dupe_target"),
        dupe_candidates[0],
    )
    dupe_bounds = dupe.get("bounds")
    if not isinstance(dupe_bounds, dict):
        raise AssertionError(f"duplicate vbar has no focus bounds: {dupe!r}")
    report["locks"]["ambiguous"] = repeated_use_lock(client, label="vbar", bounds=dupe_bounds)

    # Measured live: Side is not in the ancestry allowlist → UNKNOWN_ANCESTRY_TYPE.
    side_bounds = _wait_bounds(client, "vbar_side", timeout=3.0)
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
            poll_name="vbar side lock",
        )
        unproven = status.get("selected_lock_reason")
    if unproven != "UNKNOWN_ANCESTRY_TYPE":
        raise AssertionError(
            f"side-ancestor vbar lock was not UNKNOWN_ANCESTRY_TYPE: {unproven!r}"
        )
    report["locks"]["unproven"] = unproven

    # Step 1: resolve the editable vbar from a fresh focus rect.
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
        poll_name="vbar analysis",
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
    if source_key.get("statement_kind") != "vbar":
        raise AssertionError(f"expected vbar statement_kind: {source_key!r}")
    if host_move is not True:
        raise AssertionError(f"host did not unlock move for vbar: {analysis_status!r}")

    original = analysis_status.get("selected_original_position") or analysis_status.get(
        "selected_source_position"
    )
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        original = [int(baseline_position["x"]), int(baseline_position["y"])]
    requested_before = [int(original[0]), int(original[1])]

    # Fresh focus_list observation before preview movement.
    value_baseline = client.eval_expr("renforge_editor_vbar_value")
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
        poll_name="vbar preview moved",
    )
    requested_after = [
        int(preview_status["preview_position"][0]),
        int(preview_status["preview_position"][1]),
    ]
    after_obs = _observe_selected(client)
    value_preview = client.eval_expr("renforge_editor_vbar_value")
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
    pre_save_sha = hashlib.sha256(pre_save_bytes).hexdigest()
    pre_save_text = pre_save_bytes.decode("utf-8")
    generation_before = _source_generation(analysis_status)
    save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "vbar save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_code") == "reload_committed"
        and _source_generation(status) == generation_before + 1,
        timeout=60.0,
        poll_name="vbar save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_sha = hashlib.sha256(post_save_bytes).hexdigest()
    post_save_text = post_save_bytes.decode("utf-8")
    source_position_after = _parse_xy_from_target_line(post_save_text)
    expected_source_position = {"x": requested_after[0], "y": requested_after[1]}
    if source_position_after != expected_source_position:
        raise AssertionError(
            "source patch disagrees with requested preview position: "
            f"expected={expected_source_position!r}, observed={source_position_after!r}"
        )
    expected_text = _independent_expected_after_patch(
        pre_save_text,
        x=requested_after[0],
        y=requested_after[1],
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
        "source_position_after": source_position_after,
        "outside_coordinate_spans_identical": outside_coordinate_spans_identical,
        "matches_independent_expected": True,
        "save_request": save_request,
    }

    # Step 4: reload publication cycle (frame_id filled after rebind observation).
    report["reload"] = {
        "ok": True,
        "script_generation": _source_generation(save_status),
        "status_code": save_status.get("status_code"), "status_text": save_status.get("status_text"),
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
        poll_name="vbar post-save rebind analysis",
    )
    reload_obs = _observe_selected(client)
    value_reload = client.eval_expr("renforge_editor_vbar_value")
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
            "vbar runtime value changed during preview/reload: "
            f"baseline={value_baseline!r}, preview={value_preview!r}, reload={value_reload!r}"
        )
    report["reload"]["frame_id"] = reload_obs.get("frame_id")

    # Step 6: rebinding.
    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id")
        not in (None, analysis_status.get("current_analysis_id"))
        and (successor.get("current_source_key") or {}).get("statement_kind") == "vbar",
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
        raise AssertionError("vbar byte-identical undo did not restore the baseline")
    return report
