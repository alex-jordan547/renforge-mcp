"""Live proof for editing a target inside a `viewport` (issue #44).

The unknown was never the write chain but the geometry: a viewport clips its
child and offsets it by a scroll position, so the question is whether the
engine-provided focus rectangles stay truthful as that offset changes.

Measured on Ren'Py 8.5.3: they do. `focus_list` reports screen coordinates that
already include the scroll, and the editor's attestation compares
`runtime_rect + Δ` against a later `focus_list` rect, so both sides move
together and the existing delta arithmetic needs no viewport term.

Only the measured shape is unlocked: exactly one `viewport` in the ancestry,
with a plain `fixed` child. `scrollbars` (which wraps the viewport in a `Side`)
and nested viewports stay locked with their own reasons.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from renforge.editor.source import analyze_editable_statement
from renforge.editor_live_common import (
    center as _center,
    inject_editor_live_resources,
    observe_selected as _observe_selected,
    select_lock,
    sha256_file as _sha256_file,
    wait_bounds,
)
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)

FIXTURE_SCREEN = "renforge_editor_viewport_fixture"
TARGET_ID = "viewport_target"
VIEWPORT_ID = "viewport_frame"


def inject_editor_viewport_resources(project_root: Path) -> dict[str, str]:
    return inject_editor_live_resources(
        project_root,
        editor_basename="editor_viewport",
        fixture_filename="renforge_editor_viewport_fixture.rpy",
    )


def _adjustment(client: Any) -> str:
    return (
        f'renpy.display.screen.get_screen("{FIXTURE_SCREEN}")'
        f'.widgets["{VIEWPORT_ID}"].yadjustment'
    )


def scroll_to(client: Any, value: int) -> float:
    """Move the viewport's vertical scroll and return the applied value."""
    client.eval_expr(f"{_adjustment(client)}.change({int(value)})")
    client.eval_expr("renpy.restart_interaction()")
    return float(client.eval_expr(f"{_adjustment(client)}.value"))


def _target_line_with_offset(source_text: str) -> tuple[str, int]:
    offset = 0
    for line in source_text.splitlines(keepends=True):
        if f'id "{TARGET_ID}"' in line and line.lstrip().startswith("textbutton "):
            analyze_editable_statement(line, expected_widget_id=TARGET_ID)
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing viewport textbutton line for {TARGET_ID!r}")


def _parse_xy(source_text: str) -> dict[str, int]:
    line, _ = _target_line_with_offset(source_text)
    x = re.search(r"\bxpos\s+(-?\d+)", line)
    y = re.search(r"\bypos\s+(-?\d+)", line)
    if x is None or y is None:
        raise AssertionError(f"target line missing literal xpos/ypos: {line!r}")
    return {"x": int(x.group(1)), "y": int(y.group(1))}


def _independent_expected_after_patch(before_text: str, *, x: int, y: int) -> str:
    """Build expected fixture text without calling the production patcher."""
    line, offset = _target_line_with_offset(before_text)
    patched = re.sub(r"(\bxpos\s+)-?\d+", rf"\g<1>{int(x)}", line, count=1)
    patched = re.sub(r"(\bypos\s+)-?\d+", rf"\g<1>{int(y)}", patched, count=1)
    if patched == line:
        raise AssertionError("independent patch constructor did not change the coordinates")
    return f"{before_text[:offset]}{patched}{before_text[offset + len(line) :]}"


def _outside_coordinate_spans_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text)
    after_line, _ = _target_line_with_offset(after_text)
    normalise = lambda line: re.sub(  # noqa: E731 - local, single-expression
        r"(\bypos\s+)-?\d+",
        r"\1__Y__",
        re.sub(r"(\bxpos\s+)-?\d+", r"\1__X__", line, count=1),
        count=1,
    )
    if normalise(before_line) != normalise(after_line):
        return False
    return before_text.replace(before_line, "", 1) == after_text.replace(after_line, "", 1)


def prove_scroll_tracking(client: Any, offsets: list[int]) -> dict[str, Any]:
    """Check the engine keeps the focus rect truthful as the scroll changes.

    This is the measurement the write chain rests on: if `focus_list` did not
    already account for the scroll, every position the editor reports would be
    wrong by the offset.
    """
    samples = []
    for offset in offsets:
        applied = scroll_to(client, offset)
        bounds = wait_bounds(client, TARGET_ID, fixture_screen=FIXTURE_SCREEN)
        samples.append(
            {
                "requested_scroll": offset,
                "applied_scroll": applied,
                "rect_y": int(bounds["y"]),
            }
        )
    # Screen y must fall by exactly the distance scrolled.
    reference = samples[0]
    for sample in samples[1:]:
        expected = reference["rect_y"] - (sample["applied_scroll"] - reference["applied_scroll"])
        if abs(sample["rect_y"] - expected) > 1:
            raise AssertionError(
                "focus rect does not track viewport scroll: "
                f"at scroll {sample['applied_scroll']} expected y≈{expected}, got {sample['rect_y']}"
            )
    return {
        "samples": samples,
        "tracks_scroll": True,
    }


def run_editor_viewport_scrolled_commit(
    client: Any,
    *,
    fixture_path: Path,
    scroll: int,
) -> dict[str, Any]:
    """Attempt a commit while the viewport is scrolled and record the outcome.

    Ren'Py rebuilds the screen on `reload_script` and the viewport adjustment
    does not survive it, so the host attests a fresh geometry against a position
    derived at the old scroll. The interesting question is not whether that
    succeeds — it cannot — but whether the editor refuses safely.
    """
    baseline_bytes = fixture_path.read_bytes()
    _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    scroll_before = scroll_to(client, scroll)

    target_bounds = wait_bounds(client, TARGET_ID, fixture_screen=FIXTURE_SCREEN)
    target_center = _center(target_bounds)
    _require_ok(
        client.request(
            "editor_task0_select",
            {"x": target_center[0], "y": target_center[1]},
        ),
        "target select",
    )
    analysis_status = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="scrolled viewport analysis",
    )
    original = list(analysis_status.get("selected_original_position") or [])
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 12}), "nudge right")
    _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_position"), (list, tuple))
        and list(status.get("preview_position") or []) != original,
        timeout=8.0,
        poll_name="scrolled viewport preview moved",
    )

    _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "scrolled viewport save",
    )
    settled = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") in ("Reload committed", "Reload failed"),
        timeout=60.0,
        poll_name="scrolled viewport save settled",
    )
    scroll_after = float(client.eval_expr(f"{_adjustment(client)}.value"))
    after_bytes = fixture_path.read_bytes()
    baseline_position = _parse_xy(baseline_bytes.decode("utf-8"))
    written_position = _parse_xy(after_bytes.decode("utf-8"))
    fixture_path.write_bytes(baseline_bytes)
    return {
        "scroll_before": scroll_before,
        "scroll_after": scroll_after,
        "scroll_survived_reload": abs(scroll_after - scroll_before) <= 1,
        "status_text": settled.get("status_text"),
        "save_error": settled.get("save_error"),
        "source_unchanged": after_bytes == baseline_bytes,
        "baseline_position": baseline_position,
        "written_position": written_position,
        # The drag was 12px right, in screen space. A scroll-independent write
        # must move the authored x by exactly that and leave y alone.
        "written_delta": {
            "x": written_position["x"] - baseline_position["x"],
            "y": written_position["y"] - baseline_position["y"],
        },
    }


def run_editor_viewport_live_scenario(
    client: Any,
    *,
    fixture_path: Path,
    scroll: int,
) -> dict[str, Any]:
    """Seven-step live proof for a viewport child, at one scroll offset."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = _sha256_file(fixture_path)
    baseline_text = baseline_bytes.decode("utf-8")
    baseline_position = _parse_xy(baseline_text)
    report["fixture_before"] = {"sha256": baseline_sha, "position": baseline_position}

    _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )

    applied_scroll = scroll_to(client, scroll)
    report["scroll"] = {"requested": scroll, "applied": applied_scroll}

    # Lock matrix, measured at this same scroll offset.
    report["locks"] = {
        "computed": select_lock(
            client, "viewport_computed", "YPOS_LITERAL_REQUIRED", fixture_screen=FIXTURE_SCREEN
        ),
        "container": select_lock(
            client,
            "viewport_container",
            "CONTAINER_POSITION_UNSUPPORTED",
            fixture_screen=FIXTURE_SCREEN,
        ),
        "nested": select_lock(
            client,
            "viewport_nested_target",
            "NESTED_VIEWPORT_UNSUPPORTED",
            fixture_screen=FIXTURE_SCREEN,
        ),
    }

    # Step 1: resolve the editable target from a fresh focus rect.
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
        poll_name="viewport analysis",
    )
    source_key = analysis_status.get("current_source_key") or {}
    host_move = bool(analysis_status.get("current_analysis_id")) and analysis_status.get(
        "selected_lock_reason"
    ) in (None, "")
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "lock_reason": analysis_status.get("selected_lock_reason"),
        "move": host_move,
        "measurement_method": observation.get("measurement_method"),
        "viewport_ancestor_count": sum(
            1
            for node in (analysis_status.get("selected_runtime_key") or {}).get("ancestry") or []
            if node.get("type") == "Viewport"
        ),
    }
    if host_move is not True:
        raise AssertionError(f"host did not unlock move inside the viewport: {analysis_status!r}")

    original = analysis_status.get("selected_original_position")
    if not (isinstance(original, (list, tuple)) and len(original) == 2):
        original = [baseline_position["x"], baseline_position["y"]]
    requested_before = [int(original[0]), int(original[1])]

    before_obs = _observe_selected(client)
    before_rect = before_obs.get("rect") or []
    bounds_before = [int(before_rect[0]), int(before_rect[1])]
    frame_before = before_obs.get("frame_id")

    # Step 2: preview. Nudges stay small so the target cannot leave the frame,
    # which would drop it from focus_list mid-proof.
    _require_ok(client.request("editor_task0_key", {"key": "right", "repeat": 12}), "nudge right")
    _require_ok(client.request("editor_task0_key", {"key": "down", "repeat": 8}), "nudge down")
    preview_status = _wait_for_status(
        client,
        lambda status: isinstance(status.get("preview_position"), (list, tuple))
        and len(status.get("preview_position") or []) == 2
        and list(status.get("preview_position") or []) != requested_before,
        timeout=8.0,
        poll_name="viewport preview moved",
    )
    requested_after = [
        int(preview_status["preview_position"][0]),
        int(preview_status["preview_position"][1]),
    ]
    after_obs = _observe_selected(client)
    after_rect = after_obs.get("rect") or []
    bounds_after = [int(after_rect[0]), int(after_rect[1])]
    frame_after = after_obs.get("frame_id")
    if not frame_before or not frame_after or frame_before == frame_after:
        raise AssertionError(
            f"preview observations need distinct frame_ids: {frame_before!r} / {frame_after!r}"
        )
    requested_delta = [requested_after[axis] - requested_before[axis] for axis in (0, 1)]
    observed_delta = [bounds_after[axis] - bounds_before[axis] for axis in (0, 1)]
    if any(abs(observed_delta[axis] - requested_delta[axis]) > 1 for axis in (0, 1)):
        raise AssertionError(
            "focus_list preview bounds disagree with requested movement inside the viewport: "
            f"requested={requested_delta!r}, observed={observed_delta!r}"
        )
    report["preview"] = {
        "bounds_before": bounds_before,
        "bounds_after": bounds_after,
        "requested_before": requested_before,
        "requested_after": requested_after,
        "requested_delta": requested_delta,
        "observed_delta": observed_delta,
    }

    # Step 3: patch through the real Save control.
    pre_save_bytes = fixture_path.read_bytes()
    pre_save_text = pre_save_bytes.decode("utf-8")
    pre_save_sha = hashlib.sha256(pre_save_bytes).hexdigest()
    generation_before = _source_generation(analysis_status)
    _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "viewport save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_in_progress"))
        and status.get("status_text") in ("Reload committed", "Reload failed"),
        timeout=60.0,
        poll_name="viewport save settled",
    )
    # Measured here rather than assumed: the host attests by comparing a
    # post-reload focus rect against a position derived before the reload, so a
    # scroll that does not survive the reload invalidates the comparison.
    scroll_after_reload = float(client.eval_expr(f"{_adjustment(client)}.value"))
    report["scroll"]["after_reload"] = scroll_after_reload
    if save_status.get("status_text") != "Reload committed":
        raise AssertionError(
            f"save did not commit: {save_status.get('save_error')!r}; "
            f"viewport scroll was {applied_scroll} before the reload "
            f"and {scroll_after_reload} after"
        )
    if _source_generation(save_status) != generation_before + 1:
        raise AssertionError(f"unexpected script generation after save: {save_status!r}")
    post_save_bytes = fixture_path.read_bytes()
    post_save_text = post_save_bytes.decode("utf-8")
    post_save_sha = hashlib.sha256(post_save_bytes).hexdigest()
    source_position_after = _parse_xy(post_save_text)
    # `preview_position` is screen-space; the authored value is child-space. The
    # two coincide only when no ancestor offsets the child, which is why every
    # earlier adapter could compare them directly. Inside a viewport they differ
    # by the frame origin and the scroll, so compare the delta instead.
    expected_source_position = {
        "x": baseline_position["x"] + requested_delta[0],
        "y": baseline_position["y"] + requested_delta[1],
    }
    if source_position_after != expected_source_position:
        raise AssertionError(
            "source patch disagrees with the requested preview position: "
            f"expected={expected_source_position!r}, observed={source_position_after!r}"
        )
    if post_save_text != _independent_expected_after_patch(
        pre_save_text,
        x=expected_source_position["x"],
        y=expected_source_position["y"],
    ):
        raise AssertionError("patched fixture bytes disagree with independent expected content")
    if not _outside_coordinate_spans_identical(pre_save_text, post_save_text):
        raise AssertionError("source patch changed bytes outside the xpos/ypos spans")
    report["patch"] = {
        "before_sha256": pre_save_sha,
        "after_sha256": post_save_sha,
        "source_position_after": source_position_after,
        "outside_coordinate_spans_identical": True,
        "matches_independent_expected": True,
    }

    # Step 4: reload publication cycle.
    report["reload"] = {
        "ok": True,
        "status_text": save_status.get("status_text"),
        "generation_delta": _source_generation(save_status) - generation_before,
    }

    # Step 5: pixel agreement across the reload, still inside the viewport.
    successor = _wait_for_status(
        client,
        lambda status: bool(status.get("current_analysis_id"))
        and status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") in (None, ""),
        timeout=10.0,
        poll_name="viewport post-save rebind",
    )
    reload_obs = _observe_selected(client)
    reload_rect = reload_obs.get("rect") or []
    reload_bounds = [int(reload_rect[0]), int(reload_rect[1])]
    pixel_delta = [reload_bounds[axis] - bounds_after[axis] for axis in (0, 1)]
    if any(abs(value) > 1 for value in pixel_delta):
        raise AssertionError(
            "post-reload focus rect disagrees with post-preview focus rect: "
            f"preview={bounds_after!r}, reload={reload_bounds!r}"
        )
    report["pixel_agreement"] = {
        "preview_after": bounds_after,
        "reload_after": reload_bounds,
        "delta": pixel_delta,
    }

    # Step 6: rebinding.
    report["rebinding"] = {
        "ok": successor.get("selected_widget_id") == TARGET_ID
        and successor.get("current_analysis_id")
        not in (None, analysis_status.get("current_analysis_id"))
        and successor.get("selected_lock_reason") in (None, ""),
        "widget_id": successor.get("selected_widget_id"),
    }
    if report["rebinding"]["ok"] is not True:
        raise AssertionError(f"rebinding failed inside the viewport: {report['rebinding']!r}")

    # The scroll must not have drifted under the edit, or the pixel agreement
    # above would be comparing two different geometries.
    final_scroll = float(client.eval_expr(f"{_adjustment(client)}.value"))
    if abs(final_scroll - applied_scroll) > 1:
        raise AssertionError(
            f"viewport scroll drifted during the proof: {applied_scroll} -> {final_scroll}"
        )
    report["scroll"]["final"] = final_scroll

    # Step 7: byte-identical undo.
    fixture_path.write_bytes(baseline_bytes)
    restored_bytes = fixture_path.read_bytes()
    report["byte_identical_undo"] = {
        "matches_baseline": restored_bytes == baseline_bytes,
        "patched_differed": post_save_bytes != baseline_bytes,
    }
    if restored_bytes != baseline_bytes:
        raise AssertionError("viewport byte-identical undo did not restore the baseline")
    return report
