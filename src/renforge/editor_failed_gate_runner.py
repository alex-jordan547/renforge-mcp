"""Live evidence runner for issue #52 failed-gate UI across identity, clipping, and repetition locks."""

from __future__ import annotations

import hashlib
import io
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.editor_live_common import list_ui_info, wait_bounds

FIXTURE_SCREEN = "renforge_editor_failed_gate_fixture"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_failed_gate_fixture.rpy"
)

# Shared Gate Lock Reason Codes
LOCK_SYNTHETIC_WIDGET_ID = "SYNTHETIC_WIDGET_ID"
LOCK_TRANSFORM_CROP_COMPOSITE = "TRANSFORM_CROP_PARTIAL_UNSUPPORTED"
LOCK_LOOP_INSTANCE = "LOOP_INSTANCE_UNSUPPORTED"
LOCK_MULTI_INSTANCE = "MULTI_INSTANCE_UNSUPPORTED"
LOCK_REPEATED_USE = "REPEATED_USE_UNSUPPORTED"


def inject_editor_failed_gate_resources(project_root: Path) -> Path:
    target = project_root / "game" / "zz_renforge_editor_failed_gate_fixture.rpy"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_RESOURCE, target)
    return target


def _show_fixture(client: Any) -> None:
    last: Any = None
    for _ in range(60):
        last = client.request("editor_task0_start", {"screen": FIXTURE_SCREEN})
        if isinstance(last, dict) and last.get("ok") is True:
            return
        time.sleep(0.1)
    raise AssertionError(f"failed-gate fixture did not start: {last!r}")


def _capture_frame(client: Any, name: str, output_dir: Path | None) -> dict[str, Any]:
    png_bytes = client.screenshot()
    sha256 = hashlib.sha256(png_bytes).hexdigest()
    image = Image.open(io.BytesIO(png_bytes))
    width, height = image.size

    saved_path: str | None = None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{name}.png"
        file_path.write_bytes(png_bytes)
        saved_path = str(file_path)

    return {
        "name": name,
        "sha256": sha256,
        "width": width,
        "height": height,
        "saved_path": saved_path,
        "byte_count": len(png_bytes),
    }


def probe_locked_target(
    client: Any,
    *,
    click_x: int,
    click_y: int,
    expected_lock_reason: str,
    target_name: str,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    select_reply = client.request("editor_task0_select", {"x": click_x, "y": click_y})
    if not isinstance(select_reply, dict):
        raise AssertionError(f"select for {target_name} returned non-dict: {select_reply!r}")

    ok = select_reply.get("ok")
    lock_reason = select_reply.get("lock_reason")
    if ok is not False:
        raise AssertionError(f"expected select ok=False for {target_name}, got {select_reply!r}")
    if lock_reason != expected_lock_reason:
        raise AssertionError(
            f"expected lock_reason {expected_lock_reason!r} for {target_name}, got {lock_reason!r} (select_reply: {select_reply!r})"
        )

    label_text = str(client.eval_expr("_renforge_editor_state().label_text") or "")
    selected_rect = client.eval_expr("_renforge_editor_state().selected_rect")
    save_enabled = bool(client.eval_expr("_renforge_editor_save_enabled()"))

    if expected_lock_reason not in label_text:
        raise AssertionError(
            f"label_text for {target_name} missing lock code {expected_lock_reason!r}: {label_text!r}"
        )

    if not (isinstance(selected_rect, list) and len(selected_rect) == 4 and selected_rect[2] > 0 and selected_rect[3] > 0):
        raise AssertionError(f"selected_rect for {target_name} is not visible/measurable: {selected_rect!r}")

    if save_enabled is True:
        raise AssertionError(f"save_enabled for locked target {target_name} must be False")

    # Verify drag action fails
    drag_reply = client.request("editor_task0_drag", {"dx": 20, "dy": 20})
    drag_ok = drag_reply.get("ok") if isinstance(drag_reply, dict) else False
    if drag_ok is not False:
        raise AssertionError(f"expected drag ok=False for {target_name}, got {drag_reply!r}")

    # Frame capture
    frame = _capture_frame(client, f"failed_gate_{target_name}", output_dir)

    return {
        "target_name": target_name,
        "click": [click_x, click_y],
        "ok": ok,
        "lock_reason": lock_reason,
        "label_text": label_text,
        "selected_rect": selected_rect,
        "save_enabled": save_enabled,
        "drag_prevented": drag_ok is False,
        "frame": frame,
    }


def run_editor_failed_gate_live_scenario(
    client: Any,
    *,
    fixture_path: Path,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()

    _show_fixture(client)

    report: dict[str, Any] = {
        "baseline_sha256": baseline_sha,
        "fixture_path": str(fixture_path),
        "gate_families": {},
    }

    # 1. Missing source identity (textbutton "NO_ID")
    info = list_ui_info(client, FIXTURE_SCREEN)
    elements = info.get("elements") if isinstance(info.get("elements"), list) else []
    no_id_elem = next(
        (e for e in elements if e.get("text") == "NO_ID"),
        None,
    )
    if no_id_elem is None:
        raise AssertionError("could not find NO_ID element in list_ui_info")
    bounds = no_id_elem.get("bounds") or {}
    click_x = bounds["x"] + bounds["width"] // 2
    click_y = bounds["y"] + bounds["height"] // 2
    identity_res = probe_locked_target(
        client,
        click_x=click_x,
        click_y=click_y,
        expected_lock_reason=LOCK_SYNTHETIC_WIDGET_ID,
        target_name="identity",
        output_dir=output_dir,
    )
    report["gate_families"]["missing_identity"] = identity_res

    # 2. Clipping ancestry (textbutton "CLIPPED" inside Transform(crop, rotate))
    clip_bounds = wait_bounds(client, "clipped_composite_target", fixture_screen=FIXTURE_SCREEN)
    click_x = clip_bounds["x"] + clip_bounds["width"] // 2
    click_y = clip_bounds["y"] + clip_bounds["height"] // 2
    clipping_res = probe_locked_target(
        client,
        click_x=click_x,
        click_y=click_y,
        expected_lock_reason=LOCK_TRANSFORM_CROP_COMPOSITE,
        target_name="clipping",
        output_dir=output_dir,
    )
    report["gate_families"]["clipping_ancestry"] = clipping_res

    # 3. Repeated runtime instance (textbutton in loop)
    rep_bounds = wait_bounds(client, "repeated_loop_target", fixture_screen=FIXTURE_SCREEN)
    click_x = rep_bounds["x"] + rep_bounds["width"] // 2
    click_y = rep_bounds["y"] + rep_bounds["height"] // 2
    repetition_res = probe_locked_target(
        client,
        click_x=click_x,
        click_y=click_y,
        expected_lock_reason=LOCK_LOOP_INSTANCE,
        target_name="repetition",
        output_dir=output_dir,
    )
    report["gate_families"]["repeated_instance"] = repetition_res

    # 4. Unlocked control baseline (textbutton "UNLOCKED")
    unlocked_bounds = wait_bounds(client, "unlocked_control_target", fixture_screen=FIXTURE_SCREEN)
    click_x = unlocked_bounds["x"] + unlocked_bounds["width"] // 2
    click_y = unlocked_bounds["y"] + unlocked_bounds["height"] // 2
    unlocked_reply = client.request("editor_task0_select", {"x": click_x, "y": click_y})
    unlocked_ok = unlocked_reply.get("ok") if isinstance(unlocked_reply, dict) else False
    if unlocked_ok is not True:
        raise AssertionError(f"expected select ok=True for unlocked target, got {unlocked_reply!r}")
    unlocked_frame = _capture_frame(client, "failed_gate_unlocked", output_dir)
    report["unlocked_control"] = {
        "click": [click_x, click_y],
        "ok": unlocked_ok,
        "selected_widget_id": (unlocked_reply.get("selected") or {}).get("widget_id"),
        "frame": unlocked_frame,
    }

    # Verify source byte invariant
    after_bytes = fixture_path.read_bytes()
    after_sha = hashlib.sha256(after_bytes).hexdigest()
    report["source_unchanged"] = after_bytes == baseline_bytes
    report["after_sha256"] = after_sha

    if not report["source_unchanged"]:
        raise AssertionError("source bytes were unexpectedly modified during failed-gate probing")

    report["verdict"] = "pass"
    report["verdict_reason"] = "All locked gate families remain visible, measurable, rendered with stable lock code, and write-disabled"

    return report
