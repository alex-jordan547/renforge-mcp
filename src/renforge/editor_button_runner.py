from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_button_statement
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)

FIXTURE_SCREEN = "renforge_editor_button_fixture"
TARGET_ID = "button_target"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_button_fixture.rpy"
)


def inject_editor_button_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_button.rpy"
    fixture_target = game_dir / "zz_renforge_editor_button_fixture.rpy"
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


def _wait_bounds(client: Any, widget_id: str, *, timeout: float = 6.0) -> dict[str, int]:
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


def _target_line_with_offset(source_text: str) -> tuple[str, int, int]:
    offset = 0
    for source_line, line in enumerate(source_text.splitlines(keepends=True), start=1):
        if f'id "{TARGET_ID}"' in line and "button" in line:
            return line, source_line, offset
        offset += len(line)
    raise AssertionError(f"source missing target line for {TARGET_ID!r}")


def _child_block_bytes(source_text: str, source_line: int) -> bytes:
    lines = source_text.splitlines(keepends=True)
    if source_line < 1 or source_line > len(lines):
        raise AssertionError(f"invalid button source line: {source_line}")
    header = lines[source_line - 1]
    header_indent = len(header) - len(header.lstrip(" \t"))
    end_line = source_line
    for index in range(source_line, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            end_line = index + 1
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if indent <= header_indent:
            end_line = index
            break
        end_line = index + 1
    start_offset = sum(len(line) for line in lines[:source_line])
    end_offset = sum(len(line) for line in lines[:end_line])
    return source_text[start_offset:end_offset].encode("utf-8")


def _normalize_coordinate_spans(source_text: str) -> bytes:
    _line, source_line, line_offset = _target_line_with_offset(source_text)
    statement = analyze_button_statement(
        source_text,
        source_line=source_line,
        expected_widget_id=TARGET_ID,
    )
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


def _coordinate_spans_are_only_difference(before_text: str, after_text: str) -> bool:
    return _normalize_coordinate_spans(before_text) == _normalize_coordinate_spans(after_text)


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


def run_editor_button_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Run the seven-step live proof for the dedicated explicit-block button adapter."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
    baseline_text = baseline_bytes.decode("utf-8")
    target_line, target_source_line, _target_offset = _target_line_with_offset(baseline_text)
    baseline_statement = analyze_button_statement(
        baseline_text,
        source_line=target_source_line,
        expected_widget_id=TARGET_ID,
    )
    baseline_child = _child_block_bytes(baseline_text, target_source_line)
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "position": {"x": baseline_statement.xpos, "y": baseline_statement.ypos},
        "child_sha256": hashlib.sha256(baseline_child).hexdigest(),
        "source_line": target_source_line,
        "header": target_line.rstrip("\n"),
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
        timeout=10.0,
        poll_name="button analysis",
    )
    source_key = analysis_status.get("current_source_key") or {}
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "lock_reason": analysis_status.get("selected_lock_reason"),
        "move": source_key.get("statement_kind") == "button"
        and analysis_status.get("selected_lock_reason") in (None, ""),
        "analysis_id": analysis_status.get("current_analysis_id"),
        "measurement_method": observation.get("measurement_method"),
        "source_key": source_key,
    }
    if source_key.get("statement_kind") != "button":
        raise AssertionError(f"expected button statement_kind: {source_key!r}")

    original = analysis_status.get("selected_original_position") or analysis_status.get(
        "selected_source_position"
    )
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        original = [baseline_statement.xpos, baseline_statement.ypos]
    requested_before = [int(original[0]), int(original[1])]

    # Keep the adapter-specific locked cases as live evidence before moving the
    # editable target. Visible selections are resolved from focus_list bounds.
    report["locks"] = {
        "computed": _select_lock(client, "button_computed", "XPOS_LITERAL_REQUIRED"),
        "position_in_block": _select_lock(client, "button_in_block", "POSITION_IN_BLOCK"),
        "container": _select_lock(client, "button_container", "CONTAINER_POSITION_UNSUPPORTED"),
    }
    dupe_info = client.list_ui_elements_info(screen=FIXTURE_SCREEN)
    dupe_elements = dupe_info.get("elements") if isinstance(dupe_info, dict) else None
    dupe = _find_element(
        dupe_elements if isinstance(dupe_elements, list) else [],
        "button_dupe_target",
        wanted_text="DUPE A",
    )
    dupe_bounds = dupe.get("bounds")
    if not isinstance(dupe_bounds, dict):
        raise AssertionError(f"duplicate button has no focus bounds: {dupe!r}")
    dupe_select = client.request(
        "editor_task0_select",
        {
            "x": int(dupe_bounds["x"]) + 3,
            "y": int(dupe_bounds["y"]) + int(dupe_bounds["height"]) - 2,
        },
    )
    ambiguous = dupe_select.get("lock_reason") if isinstance(dupe_select, dict) else None
    if ambiguous in (None, "", "ANALYZING"):
        # The repetition lock is decided by the host, so it settles after select.
        status = _wait_for_status(
            client,
            lambda current: current.get("selected_lock_reason") == "REPEATED_USE_UNSUPPORTED",
            timeout=10.0,
            poll_name="button dupe lock",
        )
        ambiguous = status.get("selected_lock_reason")
    if ambiguous != "REPEATED_USE_UNSUPPORTED":
        raise AssertionError(f"duplicate button was not locked as repeated use: {dupe_select!r}")
    report["locks"]["ambiguous"] = ambiguous

    unknown = client.request(
        "editor_task0_validate_runtime_key",
        {
            "runtime_key": {
                "screen": FIXTURE_SCREEN,
                "invocation_path": [FIXTURE_SCREEN],
                "widget_id": TARGET_ID,
                "source_location": ["game/zz_renforge_editor_button_fixture.rpy", target_source_line],
                "instance_discriminator": {"kind": "static", "instance_count": 1},
                "ancestry": [
                    {
                        "index": 0,
                        "type": "UnknownWidget",
                        "source_location": ["game/zz_renforge_editor_button_fixture.rpy", target_source_line],
                        "screen_owner": "game",
                        "crop_state": "none",
                        "editor_owned": False,
                    }
                ],
            }
        },
    )
    report["locks"]["unproven"] = unknown.get("lock_reason")

    # Reselect the proven target after the lock matrix and establish the
    # preview baseline from a fresh focus_list measurement.
    target_bounds = _wait_bounds(client, TARGET_ID)
    target_center = _center(target_bounds)
    _require_ok(
        client.request(
            "editor_task0_select",
            {"x": target_center[0], "y": target_center[1]},
        ),
        "target reselect",
    )
    _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="button target reanalysis",
    )

    # Step 2: preview. Nudge only after the selected target has been resolved.
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 24}), "nudge right")
    _require_ok(client.request("editor_task0_key", {"key": "down", "repeat": 16}), "nudge down")
    preview_status = _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_position"), (list, tuple))
        and len(status.get("preview_position") or []) == 2
        and list(status.get("preview_position") or []) != requested_before,
        timeout=8.0,
        poll_name="button preview moved",
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
        "measurement_method": "focus_list",
    }

    # Step 3: patch. Capture source bytes before the save boundary.
    pre_save_bytes = fixture_path.read_bytes()
    pre_save_sha = hashlib.sha256(pre_save_bytes).hexdigest()
    pre_save_text = pre_save_bytes.decode("utf-8")
    pre_save_child = _child_block_bytes(pre_save_text, target_source_line)
    generation_before = _source_generation(analysis_status)
    save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "button save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") == "Reload committed"
        and _source_generation(status) == generation_before + 1,
        timeout=60.0,
        poll_name="button save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_sha = hashlib.sha256(post_save_bytes).hexdigest()
    post_save_text = post_save_bytes.decode("utf-8")
    post_line, post_source_line, _post_offset = _target_line_with_offset(post_save_text)
    parsed_after = analyze_button_statement(
        post_save_text,
        source_line=post_source_line,
        expected_widget_id=TARGET_ID,
    )
    source_position_after = {"x": parsed_after.xpos, "y": parsed_after.ypos}
    expected_source_position = {"x": requested_after[0], "y": requested_after[1]}
    if source_position_after != expected_source_position:
        raise AssertionError(
            "source patch disagrees with requested preview position: "
            f"expected={expected_source_position!r}, observed={source_position_after!r}"
        )
    outside_coordinate_spans_identical = _coordinate_spans_are_only_difference(
        pre_save_text,
        post_save_text,
    )
    if not outside_coordinate_spans_identical:
        raise AssertionError("source patch changed bytes outside xpos/ypos spans")
    post_save_child = _child_block_bytes(post_save_text, post_source_line)
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_position_after": source_position_after,
        "parsed_after": {"xpos": parsed_after.xpos, "ypos": parsed_after.ypos},
        "outside_coordinate_spans_identical": outside_coordinate_spans_identical,
        "child_bytes_identical": post_save_child == pre_save_child,
        "child_sha256_before": hashlib.sha256(pre_save_child).hexdigest(),
        "child_sha256_after": hashlib.sha256(post_save_child).hexdigest(),
        "header_after": post_line.rstrip("\n"),
        "save_request": save_request,
    }
    if post_save_child != pre_save_child:
        raise AssertionError("button child block changed during patch")

    # Step 4: reload.
    report["reload"] = {
        "ok": True,
        "script_generation": _source_generation(save_status),
        "status_text": save_status.get("status_text"),
        "generation_delta": _source_generation(save_status) - generation_before,
    }

    # Step 5: pixel agreement, again using fresh focus_list bounds.
    successor = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="button post-save rebind analysis",
    )
    post_bounds = _wait_bounds(client, TARGET_ID)
    expected = [source_position_after["x"], source_position_after["y"]]
    observed = [post_bounds["x"], post_bounds["y"]]
    delta = [observed[0] - expected[0], observed[1] - expected[1]]
    report["pixel_agreement"] = {
        "expected": expected,
        "observed": observed,
        "delta": delta,
        "measurement_method": "focus_list",
    }

    # Step 6: the reload must bind a new analysis to the same source identity.
    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id") not in (None, analysis_status.get("current_analysis_id")),
        "widget_id": successor.get("selected_widget_id"),
        "analysis_id": successor.get("current_analysis_id"),
        "previous_analysis_id": analysis_status.get("current_analysis_id"),
        "source_key": successor.get("current_source_key"),
    }

    # Step 7: restore the exact baseline bytes and prove both whole-file and
    # child-block identity. The running session is intentionally left alone;
    # the next isolated live run starts from the restored fixture.
    fixture_path.write_bytes(baseline_bytes)
    restored_bytes = fixture_path.read_bytes()
    restored_text = restored_bytes.decode("utf-8")
    restored_line, restored_source_line, _restored_offset = _target_line_with_offset(restored_text)
    restored_child = _child_block_bytes(restored_text, restored_source_line)
    restored_sha = _sha256_file(fixture_path)
    report["byte_identical_undo"] = {
        "baseline_sha256": baseline_sha,
        "restored_sha256": restored_sha,
        "matches_baseline": restored_sha == baseline_sha,
        "patched_differed": post_save_sha != baseline_sha,
        "child_matches_baseline": restored_child == baseline_child,
        "child_sha256_baseline": hashlib.sha256(baseline_child).hexdigest(),
        "child_sha256_restored": hashlib.sha256(restored_child).hexdigest(),
        "header_restored": restored_line.rstrip("\n"),
    }
    if restored_bytes != baseline_bytes or restored_child != baseline_child:
        raise AssertionError("button byte-identical undo did not restore the baseline")
    return report
