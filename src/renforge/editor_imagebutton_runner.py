from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_imagebutton_statement
from renforge.editor_task0_runner import (
    _extract_widget_position,
    _require_ok,
    _source_generation,
    _wait_for_status,
)

FIXTURE_SCREEN = "renforge_editor_imagebutton_fixture"
TARGET_ID = "imgbtn_target"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_imagebutton_fixture.rpy"
)


def inject_editor_imagebutton_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_imagebutton.rpy"
    fixture_target = game_dir / "zz_renforge_editor_imagebutton_fixture.rpy"
    shutil.copyfile(EDITOR_RESOURCE, editor_target)
    shutil.copyfile(FIXTURE_RESOURCE, fixture_target)
    return {
        "editor": str(editor_target),
        "fixture": str(fixture_target),
    }


def _find_element(elements: list[dict[str, Any]], wanted_id: str) -> dict[str, Any]:
    for element in elements:
        if str(element.get("id") or "") == wanted_id:
            return element
    raise AssertionError(f"missing expected element id {wanted_id!r}: {elements!r}")


def _center(bounds: dict[str, Any]) -> tuple[int, int]:
    return (
        int(bounds["x"]) + int(bounds["width"]) // 2,
        int(bounds["y"]) + int(bounds["height"]) // 2,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wait_bounds(
    client: Any,
    widget_id: str,
    *,
    timeout: float = 6.0,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        listed = client.list_ui_elements(screen=FIXTURE_SCREEN)
        last = listed if isinstance(listed, list) else []
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
        if f'id "{TARGET_ID}"' in line:
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing target line for {TARGET_ID!r}")


def _normalize_coordinate_spans(
    source_text: str,
    *,
    line_offset: int,
    statement: Any,
) -> bytes:
    replacements = [
        (
            line_offset + statement.xpos_span[0],
            line_offset + statement.xpos_span[1],
            "__RENFORGE_XPOS__",
        ),
        (
            line_offset + statement.ypos_span[0],
            line_offset + statement.ypos_span[1],
            "__RENFORGE_YPOS__",
        ),
    ]
    normalized = source_text
    for start, end, replacement in sorted(replacements, reverse=True):
        normalized = f"{normalized[:start]}{replacement}{normalized[end:]}"
    return normalized.encode("utf-8")


def _coordinate_spans_are_only_difference(
    before_text: str,
    after_text: str,
) -> bool:
    before_line, before_offset = _target_line_with_offset(before_text)
    after_line, after_offset = _target_line_with_offset(after_text)
    before_statement = analyze_imagebutton_statement(
        before_line,
        expected_widget_id=TARGET_ID,
    )
    after_statement = analyze_imagebutton_statement(
        after_line,
        expected_widget_id=TARGET_ID,
    )
    return _normalize_coordinate_spans(
        before_text,
        line_offset=before_offset,
        statement=before_statement,
    ) == _normalize_coordinate_spans(
        after_text,
        line_offset=after_offset,
        statement=after_statement,
    )


def run_editor_imagebutton_live_scenario(
    client: Any,
    *,
    fixture_path: Path,
) -> dict[str, Any]:
    """Seven-step live proof for the dedicated imagebutton adapter."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
    baseline_text = baseline_bytes.decode("utf-8")
    baseline_position = _extract_widget_position(baseline_text, TARGET_ID)
    if baseline_position is None:
        raise AssertionError(f"fixture missing {TARGET_ID} position")
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "position": baseline_position,
    }

    start = _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    report["start"] = start

    target_bounds = _wait_bounds(client, TARGET_ID)
    bounds_before = [target_bounds["x"], target_bounds["y"]]
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
        timeout=8.0,
        poll_name="imagebutton analysis",
    )
    source_key = analysis_status.get("current_source_key") or {}
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "lock_reason": analysis_status.get("selected_lock_reason"),
        "move": source_key.get("statement_kind") == "imagebutton"
        and analysis_status.get("selected_lock_reason") in (None, ""),
        "analysis_id": analysis_status.get("current_analysis_id"),
        "measurement_method": observation.get("measurement_method"),
        "source_key": source_key,
    }
    if source_key.get("statement_kind") != "imagebutton":
        raise AssertionError(f"expected imagebutton statement_kind: {source_key!r}")

    original = analysis_status.get("selected_original_position") or analysis_status.get(
        "selected_source_position"
    )
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        original = [int(baseline_position["x"]), int(baseline_position["y"])]
    requested_before = [int(original[0]), int(original[1])]

    # Preview: nudge without crossing a save boundary. The verdict below is
    # based on fresh focus bounds, not the editor's requested preview state.
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 24}), "nudge right")
    _require_ok(client.request("editor_task0_key", {"key": "down", "repeat": 16}), "nudge down")
    preview_status = _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_position"), (list, tuple))
        and len(status.get("preview_position") or []) == 2
        and list(status.get("preview_position") or []) != requested_before,
        timeout=6.0,
        poll_name="preview moved",
    )
    requested_after = [
        int(preview_status["preview_position"][0]),
        int(preview_status["preview_position"][1]),
    ]
    preview_bounds = _wait_bounds(client, TARGET_ID)
    bounds_after = [preview_bounds["x"], preview_bounds["y"]]
    requested_delta = [
        requested_after[axis] - requested_before[axis]
        for axis in (0, 1)
    ]
    observed_delta = [
        bounds_after[axis] - bounds_before[axis]
        for axis in (0, 1)
    ]
    if any(
        abs(observed_delta[axis] - requested_delta[axis]) > 1
        for axis in (0, 1)
    ):
        raise AssertionError(
            "focus_list preview bounds disagree with requested movement: "
            f"requested={requested_delta!r}, observed={observed_delta!r}"
        )
    report["preview"] = {
        "before": bounds_before,
        "after": bounds_after,
        "bounds_before": bounds_before,
        "bounds_after": bounds_after,
        "requested_before": requested_before,
        "requested_after": requested_after,
        "requested_delta": requested_delta,
        "observed_delta": observed_delta,
        "measurement_method": "focus_list",
    }

    pre_save_bytes = fixture_path.read_bytes()
    pre_save_sha = hashlib.sha256(pre_save_bytes).hexdigest()
    pre_save_text = pre_save_bytes.decode("utf-8")
    generation_before = _source_generation(analysis_status)
    save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == generation_before + 1,
        timeout=60.0,
        poll_name="imagebutton save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_sha = hashlib.sha256(post_save_bytes).hexdigest()
    post_save_text = post_save_bytes.decode("utf-8")
    source_position_after = _extract_widget_position(post_save_text, TARGET_ID)
    if source_position_after is None:
        raise AssertionError("post-save source missing target position")
    expected_source_position = {
        "x": requested_after[0],
        "y": requested_after[1],
    }
    if source_position_after != expected_source_position:
        raise AssertionError(
            "source patch disagrees with requested preview position: "
            f"expected={expected_source_position!r}, observed={source_position_after!r}"
        )

    target_line, _target_offset = _target_line_with_offset(post_save_text)
    parsed_after = analyze_imagebutton_statement(target_line, expected_widget_id=TARGET_ID)
    outside_coordinate_spans_identical = _coordinate_spans_are_only_difference(
        pre_save_text,
        post_save_text,
    )
    if not outside_coordinate_spans_identical:
        raise AssertionError("source patch changed bytes outside xpos/ypos spans")
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_position_after": source_position_after,
        "parsed_after": {"xpos": parsed_after.xpos, "ypos": parsed_after.ypos},
        "save_request": save_request,
        "outside_coordinate_spans_identical": outside_coordinate_spans_identical,
    }

    report["reload"] = {
        "ok": True,
        "script_generation": _source_generation(save_status),
        "status_text": save_status.get("status_text"),
        "generation_delta": _source_generation(save_status) - generation_before,
    }

    successor = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=8.0,
        poll_name="post-save rebind analysis",
    )
    post_bounds = _wait_bounds(client, TARGET_ID)
    expected = [int(source_position_after["x"]), int(source_position_after["y"])]
    observed = [int(post_bounds["x"]), int(post_bounds["y"])]
    delta = [observed[0] - expected[0], observed[1] - expected[1]]
    report["pixel_agreement"] = {
        "expected": expected,
        "observed": observed,
        "delta": delta,
        "measurement_method": "focus_list",
    }
    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id") not in (None, analysis_status.get("current_analysis_id")),
        "widget_id": successor.get("selected_widget_id"),
        "analysis_id": successor.get("current_analysis_id"),
        "previous_analysis_id": analysis_status.get("current_analysis_id"),
        "source_key": successor.get("current_source_key"),
    }

    # Byte-identical undo evidence: restore the pre-patch fixture bytes and
    # verify SHA-256 identity with the captured baseline.
    fixture_path.write_bytes(baseline_bytes)
    restored_sha = _sha256_file(fixture_path)
    report["byte_identical_undo"] = {
        "baseline_sha256": baseline_sha,
        "restored_sha256": restored_sha,
        "matches_baseline": restored_sha == baseline_sha,
        "patched_differed": post_save_sha != baseline_sha,
    }
    return report
