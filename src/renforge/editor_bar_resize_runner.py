from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from renforge.editor_bar_runner import (
    FIXTURE_SCREEN,
    TARGET_ID,
    inject_editor_bar_resources,
    _center,
    _find_element,
    _list_info,
    _observe_selected,
    _sha256_file,
    _wait_bounds,
)
from renforge.editor_task0_runner import _require_ok, _source_generation, _wait_for_status


def _select_resize_lock(client: Any, widget_id: str, expected_code: str) -> dict[str, Any]:
    """Select a move-unlocked bar and require a specific resize lock code."""
    bounds = _wait_bounds(client, widget_id)
    selection = client.request(
        "editor_task0_select",
        {"x": _center(bounds)[0], "y": _center(bounds)[1]},
    )
    if not isinstance(selection, dict):
        raise AssertionError(f"select returned non-dict for {widget_id!r}: {selection!r}")

    def _matched(status: dict[str, Any]) -> bool:
        if status.get("selected_widget_id") != widget_id:
            return False
        if status.get("selected_lock_reason") not in (None, ""):
            return False
        if not status.get("current_analysis_id"):
            return False
        caps = status.get("current_capabilities") or {}
        if caps.get("move") is not True or caps.get("resize") is not False:
            return False
        source_key = status.get("current_source_key") or {}
        reason = source_key.get("resize_lock_reason") or {}
        return isinstance(reason, dict) and reason.get("code") == expected_code

    status = _wait_for_status(
        client,
        _matched,
        timeout=10.0,
        poll_name=f"{widget_id} resize lock",
    )
    source_key = status.get("current_source_key") or {}
    reason = source_key.get("resize_lock_reason") or {}
    if not isinstance(reason, dict) or reason.get("code") != expected_code:
        raise AssertionError(
            f"unexpected resize lock for {widget_id!r}: status={status!r} selection={selection!r}"
        )
    return {
        "code": expected_code,
        "move": (status.get("current_capabilities") or {}).get("move"),
        "resize": (status.get("current_capabilities") or {}).get("resize"),
        "size_mode": source_key.get("size_mode"),
        "resize_lock_reason": reason,
    }


def _target_line_with_offset(source_text: str) -> tuple[str, int]:
    offset = 0
    for line in source_text.splitlines(keepends=True):
        if f'id "{TARGET_ID}"' in line and line.lstrip().startswith("bar "):
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing target line for {TARGET_ID!r}")


def _parse_size_from_target_line(source_text: str) -> dict[str, int]:
    line, _ = _target_line_with_offset(source_text)
    xsize = re.search(r"\bxsize\s+(-?\d+)", line)
    ysize = re.search(r"\bysize\s+(-?\d+)", line)
    if not xsize or not ysize:
        raise AssertionError(f"target line missing xsize/ysize: {line!r}")
    return {"w": int(xsize.group(1)), "h": int(ysize.group(1))}


def _independent_expected_after_size_patch(before_text: str, *, w: int, h: int) -> str:
    line, offset = _target_line_with_offset(before_text)
    patched_line = re.sub(r"(\bxsize\s+)-?\d+", rf"\g<1>{int(w)}", line, count=1)
    patched_line = re.sub(r"(\bysize\s+)-?\d+", rf"\g<1>{int(h)}", patched_line, count=1)
    if patched_line == line:
        raise AssertionError("independent size patch constructor did not change target line")
    return f"{before_text[:offset]}{patched_line}{before_text[offset + len(line) :]}"


def _outside_size_spans_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text)
    after_line, _ = _target_line_with_offset(after_text)
    before_norm = re.sub(r"(\bxsize\s+)-?\d+", r"\1__W__", before_line, count=1)
    before_norm = re.sub(r"(\bysize\s+)-?\d+", r"\1__H__", before_norm, count=1)
    after_norm = re.sub(r"(\bxsize\s+)-?\d+", r"\1__W__", after_line, count=1)
    after_norm = re.sub(r"(\bysize\s+)-?\d+", r"\1__H__", after_norm, count=1)
    if before_norm != after_norm:
        return False
    before_rest = before_text.replace(before_line, "", 1)
    after_rest = after_text.replace(after_line, "", 1)
    return before_rest == after_rest


def run_editor_bar_resize_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Seven-step live proof for bar xsize/ysize resize (issue #47)."""
    report: dict[str, Any] = {"locks": {}}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()

    _require_ok(client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}), "start editor")
    _wait_for_status(client, lambda status: bool(status.get("active")), timeout=10.0, poll_name="editor active")

    # Resize lock matrix first: move stays unlocked, resize must stay locked with
    # the exact host-visible code (source_key.resize_lock_reason + capabilities).
    report["locks"] = {
        "xysize": _select_resize_lock(client, "bar_xysize", "BAR_XYSIZE_UNSUPPORTED"),
        "constraint": _select_resize_lock(
            client, "bar_size_constraint", "BAR_SIZE_CONSTRAINT_UNSUPPORTED"
        ),
    }

    target_bounds = _wait_bounds(client, TARGET_ID)
    baseline_position = {
        "x": int(target_bounds["x"]),
        "y": int(target_bounds["y"]),
        "width": int(target_bounds["width"]),
        "height": int(target_bounds["height"]),
    }
    target_center = _center(target_bounds)

    select = _require_ok(
        client.request("editor_task0_select", {"x": target_center[0], "y": target_center[1]}),
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
        poll_name="bar resize analysis",
    )
    source_key = analysis_status.get("current_source_key") or {}
    capabilities = analysis_status.get("current_capabilities") or {}
    host_resize = capabilities.get("resize")
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "size_mode": source_key.get("size_mode"),
        "lock_reason": analysis_status.get("selected_lock_reason"),
        "resize": host_resize,
        "move": capabilities.get("move"),
        "analysis_id": analysis_status.get("current_analysis_id"),
        "measurement_method": observation.get("measurement_method"),
        "frame_id": observation.get("frame_id"),
        "source_key": source_key,
        "original_size": analysis_status.get("selected_original_size")
        or analysis_status.get("selected_source_size"),
    }
    if source_key.get("statement_kind") != "bar":
        raise AssertionError(f"expected bar statement_kind: {source_key!r}")
    if source_key.get("size_mode") != "xsize_ysize":
        raise AssertionError(f"expected size_mode xsize_ysize: {source_key!r}")
    if host_resize is not True:
        raise AssertionError(f"host did not unlock resize for bar: {analysis_status!r}")

    original_size = analysis_status.get("selected_original_size") or analysis_status.get(
        "selected_source_size"
    )
    if not (isinstance(original_size, (list, tuple)) and len(original_size) == 2):
        original_size = [int(baseline_position["width"]), int(baseline_position["height"])]
    requested_before = [int(original_size[0]), int(original_size[1])]

    before_obs = _observe_selected(client)
    before_rect = before_obs.get("rect") or []
    if len(before_rect) < 4:
        raise AssertionError(f"pre-preview observation missing size: {before_obs!r}")
    bounds_before = [int(before_rect[2]), int(before_rect[3])]
    frame_before = before_obs.get("frame_id")

    # Step 2: preview resize via dedicated size handler (+40w, +8h).
    _require_ok(client.request("editor_task0_size", {"dw": 40, "dh": 8}), "resize wider/taller")
    preview_status = _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_size"), (list, tuple))
        and len(status.get("preview_size") or []) == 2
        and list(status.get("preview_size") or []) != requested_before,
        timeout=8.0,
        poll_name="bar resize preview",
    )
    requested_after = [
        int(preview_status["preview_size"][0]),
        int(preview_status["preview_size"][1]),
    ]
    after_obs = _observe_selected(client)
    after_rect = after_obs.get("rect") or []
    if len(after_rect) < 4:
        raise AssertionError(f"post-preview observation missing size: {after_obs!r}")
    bounds_after = [int(after_rect[2]), int(after_rect[3])]
    frame_after = after_obs.get("frame_id")
    if not frame_before or not frame_after or frame_before == frame_after:
        raise AssertionError(
            "preview observations must have distinct real frame_ids: "
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
            "focus_list preview size disagrees with requested resize: "
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
        "bar resize save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == generation_before + 1,
        timeout=60.0,
        poll_name="bar resize save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_sha = hashlib.sha256(post_save_bytes).hexdigest()
    post_save_text = post_save_bytes.decode("utf-8")
    source_size_after = _parse_size_from_target_line(post_save_text)
    expected_source_size = {"w": requested_after[0], "h": requested_after[1]}
    if source_size_after != expected_source_size:
        raise AssertionError(
            "source patch disagrees with requested preview size: "
            f"expected={expected_source_size!r}, observed={source_size_after!r}"
        )
    expected_text = _independent_expected_after_size_patch(
        pre_save_text,
        w=requested_after[0],
        h=requested_after[1],
    )
    if post_save_text != expected_text:
        raise AssertionError("patched fixture bytes disagree with independent expected content")
    outside_size_spans_identical = _outside_size_spans_identical(pre_save_text, post_save_text)
    if not outside_size_spans_identical:
        raise AssertionError("source patch changed bytes outside xsize/ysize spans")
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_size_after": source_size_after,
        "outside_size_spans_identical": outside_size_spans_identical,
        "matches_independent_expected": True,
        "save_request": save_request,
    }

    report["reload"] = {
        "ok": True,
        "script_generation": _source_generation(save_status),
        "status_text": save_status.get("status_text"),
        "generation_delta": _source_generation(save_status) - generation_before,
        "pending_handshake_sent": save_status.get("pending_handshake_sent"),
        "frame_id": None,
    }

    successor = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="bar resize post-save rebind analysis",
    )
    reload_obs = _observe_selected(client)
    reload_rect = reload_obs.get("rect") or []
    if len(reload_rect) < 4:
        raise AssertionError(f"post-reload observation missing size: {reload_obs!r}")
    reload_bounds = [int(reload_rect[2]), int(reload_rect[3])]
    pixel_delta = [
        reload_bounds[0] - bounds_after[0],
        reload_bounds[1] - bounds_after[1],
    ]
    if any(abs(value) > 1 for value in pixel_delta):
        raise AssertionError(
            "post-reload focus size disagrees with post-preview focus size: "
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
    report["reload"]["frame_id"] = reload_obs.get("frame_id")

    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id")
        not in (None, analysis_status.get("current_analysis_id"))
        and (successor.get("current_source_key") or {}).get("statement_kind") == "bar"
        and (successor.get("current_source_key") or {}).get("size_mode") == "xsize_ysize"
        and successor.get("selected_lock_reason") in (None, "")
        and (successor.get("current_capabilities") or {}).get("resize") is True,
        "widget_id": successor.get("selected_widget_id"),
        "analysis_id": successor.get("current_analysis_id"),
        "previous_analysis_id": analysis_status.get("current_analysis_id"),
        "source_key": successor.get("current_source_key"),
        "lock_reason": successor.get("selected_lock_reason"),
        "capabilities": successor.get("current_capabilities"),
    }
    if report["rebinding"]["ok"] is not True:
        raise AssertionError(f"rebinding failed: {report['rebinding']!r}")

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
        raise AssertionError("bar resize byte-identical undo did not restore the baseline")
    return report
