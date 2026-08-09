"""Live evidence runner for issue #50 text colour product path."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.editor_runner_status import is_reload_committed
from renforge.editor.source import (
    TEXT_STYLE_COLOR_MODE_LITERAL,
    analyze_text_color_style,
    apply_text_color_patch,
)
from renforge.editor_task0_runner import _require_ok, _wait_for_status

FIXTURE_SCREEN = "renforge_editor_style_fixture"
TARGET_ID = "style_color_target"
INHERITED_ID = "style_color_inherited"
EXPR_ID = "style_color_expr"
ALPHA_ID = "style_color_alpha"
LOOP_ID = "style_color_loop"
FOCUS_ID = "style_focus_control"
BASELINE_COLOR = "#e22b2b"
REQUESTED_COLOR = "#2457d6"
TARGET_LABEL = "STYLE"
# Authored placement — used only as a search seed, not as colour evidence.
_TARGET_AUTHORED = {"x": 240, "y": 220, "width": 320, "height": 120}
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_style_fixture.rpy"
)


def inject_editor_style_resources(project_root: Path) -> Path:
    """Install the style fixture only (editor comes from launch_with_bridge)."""
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    fixture_target = game_dir / "zz_renforge_editor_style_fixture.rpy"
    shutil.copyfile(FIXTURE_RESOURCE, fixture_target)
    return fixture_target


def _target_line_with_offset(source_text: str, widget_id: str) -> tuple[str, int]:
    offset = 0
    for line in source_text.splitlines(keepends=True):
        if line.lstrip().startswith("text ") and f'id "{widget_id}"' in line:
            return line, offset
        offset += len(line)
    raise AssertionError(f"source missing text line for {widget_id!r}")


def _parse_color_from_target_line(source_text: str, widget_id: str) -> str | None:
    line, _ = _target_line_with_offset(source_text, widget_id)
    match = re.search(r'\bcolor\s+([\'"])(#[0-9A-Fa-f]{3,8})\1', line)
    if not match:
        return None
    return match.group(2).lower()


def _independent_expected_after_color_patch(before_text: str, *, color: str) -> str:
    line, offset = _target_line_with_offset(before_text, TARGET_ID)
    parsed = analyze_text_color_style(line, expected_widget_id=TARGET_ID)
    patched_line = apply_text_color_patch(line.encode("utf-8"), parsed, color=color).decode("utf-8")
    if patched_line == line:
        raise AssertionError("independent colour patch constructor did not change target line")
    return f"{before_text[:offset]}{patched_line}{before_text[offset + len(line) :]}"


def _outside_color_span_identical(before_text: str, after_text: str) -> bool:
    before_line, _ = _target_line_with_offset(before_text, TARGET_ID)
    after_line, _ = _target_line_with_offset(after_text, TARGET_ID)
    before_norm = re.sub(
        r'(\bcolor\s+)([\'"])(#[0-9A-Fa-f]{3,8})\2',
        r"\1\2__COLOR__\2",
        before_line,
        count=1,
    )
    after_norm = re.sub(
        r'(\bcolor\s+)([\'"])(#[0-9A-Fa-f]{3,8})\2',
        r"\1\2__COLOR__\2",
        after_line,
        count=1,
    )
    if before_norm != after_norm:
        return False
    return before_text.replace(before_line, "", 1) == after_text.replace(after_line, "", 1)


def _show_fixture(client: Any) -> None:
    last: Any = None
    for _ in range(60):
        last = client.request("editor_task0_start", {"screen": FIXTURE_SCREEN})
        if isinstance(last, dict) and last.get("ok") is True:
            return
        time.sleep(0.1)
    raise AssertionError(f"style fixture did not start: {last!r}")


def _scene_text_bounds(client: Any, *, label: str) -> dict[str, int] | None:
    """Return scene_tree bounds for a text label when available (geometry only)."""
    tree = client.scene_tree(types=["text"], detail="semantic")
    nodes = tree.get("nodes") if isinstance(tree, dict) else None
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("text") or "") != label:
            continue
        bounds = node.get("bounds")
        if isinstance(bounds, dict) and int(bounds.get("width") or 0) > 0 and int(bounds.get("height") or 0) > 0:
            return {
                "x": int(bounds["x"]),
                "y": int(bounds["y"]),
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
            }
    return None


def _dominant_from_rgb(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    # Require a clear channel lead so anti-aliased greys stay "unknown".
    if red > blue + 30 and red > green + 30 and red >= 80:
        return "red"
    if blue > red + 30 and blue > green + 30 and blue >= 80:
        return "blue"
    return "unknown"


def _sample_region_paint(
    client: Any,
    bounds: dict[str, int],
    *,
    image: Image.Image | None = None,
) -> dict[str, Any]:
    """Independent screenshot paint stats for saturated non-dark pixels."""
    if image is None:
        image = Image.open(io.BytesIO(client.screenshot())).convert("RGB")
    x0 = max(0, int(bounds["x"]))
    y0 = max(0, int(bounds["y"]))
    x1 = min(image.width, x0 + max(1, int(bounds["width"])))
    y1 = min(image.height, y0 + max(1, int(bounds["height"])))
    painted: list[tuple[int, int, int]] = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = image.getpixel((x, y))
            if isinstance(pixel, int):
                rgb = (pixel, pixel, pixel)
            else:
                rgb = (int(pixel[0]), int(pixel[1]), int(pixel[2]))
            if max(rgb) < 50:
                continue
            if max(rgb) - min(rgb) < 25:
                continue
            painted.append(rgb)
    if not painted:
        return {
            "paint_count": 0,
            "mean_rgb": None,
            "dominant": "unknown",
            "image_size": [image.width, image.height],
            "bounds": [x0, y0, x1 - x0, y1 - y0],
            "sample_point": None,
            "sample_rgb": None,
        }
    mean = (
        round(sum(p[0] for p in painted) / len(painted)),
        round(sum(p[1] for p in painted) / len(painted)),
        round(sum(p[2] for p in painted) / len(painted)),
    )
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    sample_point = None
    sample_rgb = None
    best = -1
    for y in range(max(y0, cy - 20), min(y1, cy + 21)):
        for x in range(max(x0, cx - 40), min(x1, cx + 41)):
            pixel = image.getpixel((x, y))
            if isinstance(pixel, int):
                rgb = (pixel, pixel, pixel)
            else:
                rgb = (int(pixel[0]), int(pixel[1]), int(pixel[2]))
            chroma = max(rgb) - min(rgb)
            if chroma > best and max(rgb) >= 50:
                best = chroma
                sample_point = [x, y]
                sample_rgb = list(rgb)
    return {
        "paint_count": len(painted),
        "mean_rgb": list(mean),
        "dominant": _dominant_from_rgb(mean),
        "image_size": [image.width, image.height],
        "bounds": [x0, y0, x1 - x0, y1 - y0],
        "sample_point": sample_point,
        "sample_rgb": sample_rgb,
        "sample_dominant": _dominant_from_rgb(tuple(sample_rgb)) if sample_rgb else "unknown",
    }


def _wait_paint(
    client: Any,
    *,
    expected_dominant: str,
    timeout: float = 8.0,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Wait for expected paint observed inside live scene-tree bounds."""
    deadline = time.monotonic() + timeout
    last_bounds = dict(_TARGET_AUTHORED)
    bounds_from_scene_tree = False
    last_paint: dict[str, Any] = {
        "dominant": "unknown",
        "paint_count": 0,
        "bounds_from_scene_tree": False,
    }
    while time.monotonic() < deadline:
        observed_bounds = _scene_text_bounds(client, label=TARGET_LABEL)
        if observed_bounds is not None:
            last_bounds = observed_bounds
            bounds_from_scene_tree = True
        last_paint = {
            **_sample_region_paint(client, last_bounds),
            "bounds_from_scene_tree": bounds_from_scene_tree,
        }
        dominant = last_paint.get("sample_dominant") or last_paint.get("dominant")
        if (
            bounds_from_scene_tree
            and dominant == expected_dominant
            and int(last_paint.get("paint_count") or 0) > 20
        ):
            last_paint = {**last_paint, "dominant": dominant}
            return last_bounds, last_paint
        time.sleep(0.1)
    last_paint = {
        **last_paint,
        "dominant": last_paint.get("sample_dominant") or last_paint.get("dominant"),
        "bounds_from_scene_tree": bounds_from_scene_tree,
    }
    return last_bounds, last_paint


def _source_generation(status: dict[str, Any]) -> int:
    try:
        return int(status.get("script_generation"))
    except Exception:
        return 0


def _attempt_product_select(client: Any, bounds: dict[str, int]) -> dict[str, Any]:
    """Click via the production select path (focus + non-focusable text)."""
    x = int(bounds["x"]) + max(1, int(bounds["width"]) // 2)
    y = int(bounds["y"]) + max(1, int(bounds["height"]) // 2)
    selection = client.request("editor_task0_select", {"x": x, "y": y})
    status = client.request("editor_task0_status", {})
    selected_id = None
    if isinstance(selection, dict):
        selected = selection.get("selected") or {}
        if isinstance(selected, dict):
            selected_id = selected.get("widget_id")
        if selected_id is None:
            selected_id = selection.get("selected_widget_id")
    if selected_id is None and isinstance(status, dict):
        selected_id = status.get("selected_widget_id")
    capabilities = (status or {}).get("current_capabilities") if isinstance(status, dict) else None
    source_key = (status or {}).get("current_source_key") if isinstance(status, dict) else None
    return {
        "point": [x, y],
        "selection_ok": bool(isinstance(selection, dict) and selection.get("ok") is True),
        "selected_widget_id": selected_id,
        "selected_lock_reason": (
            (status or {}).get("selected_lock_reason") if isinstance(status, dict) else None
        ),
        "capabilities": capabilities if isinstance(capabilities, dict) else {},
        "source_key": source_key if isinstance(source_key, dict) else {},
        "status_active": bool(isinstance(status, dict) and status.get("active")),
        "save_enabled": bool(isinstance(status, dict) and status.get("save_enabled")),
        "history_length": int((status or {}).get("history_length") or 0) if isinstance(status, dict) else 0,
        "pending_transaction_id": (
            (status or {}).get("pending_transaction_id") if isinstance(status, dict) else None
        ),
        "observation": (selection or {}).get("observation") if isinstance(selection, dict) else None,
    }


def _wait_style_analysis(client: Any, *, timeout: float = 20.0) -> dict[str, Any]:
    return _wait_for_status(
        client,
        lambda status: (
            status.get("selected_widget_id") == TARGET_ID
            and status.get("selected_lock_reason") in (None, "")
            and bool(status.get("current_analysis_id"))
            and (status.get("current_source_key") or {}).get("statement_kind") == "text"
            and (status.get("current_capabilities") or {}).get("style_color") is True
        ),
        timeout=timeout,
        poll_name="style color analysis unlock",
    )


def _runtime_target_probe(client: Any, *, label: str, widget_id: str) -> dict[str, Any]:
    bounds = _scene_text_bounds(client, label=label)
    if bounds is None:
        return {"ok": False, "reason": "scene_bounds_missing", "widget_id": widget_id}
    selection = _attempt_product_select(client, bounds)
    try:
        status = _wait_for_status(
            client,
            lambda item: (
                item.get("selected_widget_id") == widget_id
                and item.get("selected_lock_reason") != "ANALYZING"
                and (
                    bool(item.get("current_analysis_id"))
                    or item.get("selected_lock_reason") not in (None, "")
                )
            ),
            timeout=20.0,
            poll_name=f"style runtime probe {widget_id}",
        )
    except AssertionError as exc:
        final_status = client.request("editor_task0_status", {})
        return {
            "ok": False,
            "reason": str(exc),
            "widget_id": widget_id,
            "selection": selection,
            "final_status": {
                "selected_widget_id": final_status.get("selected_widget_id"),
                "selected_analysis_pending": final_status.get("selected_analysis_pending"),
                "selected_lock_reason": final_status.get("selected_lock_reason"),
                "current_analysis_id": final_status.get("current_analysis_id"),
                "save_error": final_status.get("save_error"),
                "status_code": final_status.get("status_code"), "status_text": final_status.get("status_text"),
            },
        }
    return {
        "ok": True,
        "widget_id": widget_id,
        "selection": selection,
        "lock_reason": status.get("selected_lock_reason"),
        "capabilities": status.get("current_capabilities") or {},
        "source_key": status.get("current_source_key") or {},
        "observation": selection.get("observation") or {},
    }


def _lock_probe(source_text: str, widget_id: str, expected_code: str) -> dict[str, Any]:
    line, _ = _target_line_with_offset(source_text, widget_id)
    parsed = analyze_text_color_style(line, expected_widget_id=widget_id)
    return {
        "widget_id": widget_id,
        "style_mode": parsed.style_mode,
        "style_lock_code": parsed.style_lock_code,
        "matches_expected": parsed.style_lock_code == expected_code,
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_editor_style_color_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Evidence-gated live scenario for text colour product path (issue #50)."""
    baseline = fixture_path.read_bytes()
    baseline_sha = _sha256_bytes(baseline)
    baseline_text = baseline.decode("utf-8")
    report: dict[str, Any] = {
        "baseline_sha256": baseline_sha,
        "adapter": "text",
        "property": "color",
        "style_mode": TEXT_STYLE_COLOR_MODE_LITERAL,
    }

    _show_fixture(client)

    report["locks"] = {
        "inherited": _lock_probe(baseline_text, INHERITED_ID, "STYLE_COLOR_NOT_DIRECTLY_AUTHORED"),
        "expression": _lock_probe(baseline_text, EXPR_ID, "STYLE_COLOR_LITERAL_REQUIRED"),
    }

    alpha_probe = _runtime_target_probe(client, label="ALPHA", widget_id=ALPHA_ID)
    alpha_observation = alpha_probe.get("observation") or {}
    alpha_caps = alpha_probe.get("capabilities") or {}
    report["runtime_alpha"] = {
        **alpha_probe,
        "ok": bool(
            alpha_probe.get("ok")
            and alpha_probe.get("lock_reason") in (None, "")
            and alpha_caps.get("style_color") is True
            and alpha_observation.get("style_color") == "#33669980"
        ),
    }

    loop_probe = _runtime_target_probe(client, label="LOOP 1", widget_id=LOOP_ID)
    loop_observation = loop_probe.get("observation") or {}
    loop_runtime_key = loop_observation.get("runtime_key") or {}
    loop_discriminator = loop_runtime_key.get("instance_discriminator") or {}
    report["runtime_repeated_lock"] = {
        **loop_probe,
        "instance_discriminator": loop_discriminator,
        "ok": bool(
            loop_probe.get("ok")
            and loop_probe.get("lock_reason")
            in ("LOOP_INSTANCE_UNSUPPORTED", "MULTI_INSTANCE_UNSUPPORTED")
            and (loop_probe.get("capabilities") or {}).get("style_color") is not True
            and int(loop_discriminator.get("instance_count") or 0) >= 2
        ),
    }

    target_line, _ = _target_line_with_offset(baseline_text, TARGET_ID)
    target_analysis = analyze_text_color_style(target_line, expected_widget_id=TARGET_ID)
    report["resolve_source"] = {
        "widget_id": target_analysis.widget_id,
        "color": target_analysis.color,
        "style_mode": target_analysis.style_mode,
        "style_lock_code": target_analysis.style_lock_code,
        "unlocked": (
            target_analysis.style_mode == TEXT_STYLE_COLOR_MODE_LITERAL
            and target_analysis.style_lock_code is None
            and target_analysis.color == BASELINE_COLOR
        ),
    }
    if not report["resolve_source"]["unlocked"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_source_unlock_failed"
        return report

    bounds_before, pixel_before = _wait_paint(client, expected_dominant="red", timeout=8.0)
    report["target_bounds"] = bounds_before
    report["pixel_before"] = pixel_before

    # Focusable control still resolves via focus_list (control-path sanity).
    focus_bounds = None
    try:
        info = client.list_ui_elements_info(screen=FIXTURE_SCREEN)
        elements = info.get("elements") if isinstance(info, dict) else []
        for element in elements or []:
            if str(element.get("id") or "") == FOCUS_ID:
                b = element.get("bounds") or {}
                focus_bounds = {
                    "x": int(b["x"]),
                    "y": int(b["y"]),
                    "width": int(b["width"]),
                    "height": int(b["height"]),
                }
                break
    except Exception as exc:  # noqa: BLE001 — evidence plane
        report["focus_control_error"] = str(exc)
    report["focus_control_bounds"] = focus_bounds
    if focus_bounds is not None:
        report["focus_control_select"] = _attempt_product_select(client, focus_bounds)

    # --- Product select on non-focusable text ---
    report["product_select"] = _attempt_product_select(client, bounds_before)
    try:
        analysis_status = _wait_style_analysis(client, timeout=20.0)
    except AssertionError as exc:
        report["product_select_analysis_error"] = str(exc)
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_select_unlock_failed"
        return report

    caps = analysis_status.get("current_capabilities") or {}
    source_key = analysis_status.get("current_source_key") or {}
    report["product_select_unlocked_style"] = bool(
        analysis_status.get("selected_widget_id") == TARGET_ID
        and source_key.get("statement_kind") == "text"
        and source_key.get("style_mode") == TEXT_STYLE_COLOR_MODE_LITERAL
        and caps.get("style_color") is True
        and caps.get("style_color_preview") is True
        and caps.get("style_color_commit") is True
        and caps.get("style_color_undo") is True
        and caps.get("style_color_attestation_rollback") is True
    )
    report["product_seam_probe"] = {
        "measurement_source": "editor_task0_status.current_capabilities",
        "selected_widget_id": analysis_status.get("selected_widget_id"),
        "capabilities": caps,
        "source_key": source_key,
        "save_enabled": analysis_status.get("save_enabled"),
        "history_length": analysis_status.get("history_length"),
        "analysis_id": analysis_status.get("current_analysis_id"),
        "measurement_method": ((report["product_select"].get("observation") or {}) or {}).get(
            "measurement_method"
        ),
    }
    report["product_preview_available"] = bool(caps.get("style_color_preview") is True)
    report["product_commit_available"] = bool(caps.get("style_color_commit") is True)
    report["product_undo_available"] = bool(caps.get("style_color_undo") is True)
    report["refused_attestation_rollback_available"] = bool(
        caps.get("style_color_attestation_rollback") is True
    )
    if not report["product_select_unlocked_style"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_path_missing"
        return report

    generation0 = _source_generation(analysis_status)

    # --- Preview without source write ---
    pre_preview_bytes = fixture_path.read_bytes()
    preview = _require_ok(
        client.request("editor_task0_style_color", {"color": REQUESTED_COLOR}),
        "style color preview",
    )
    preview_status = _wait_for_status(
        client,
        lambda status: bool(status.get("save_enabled"))
        and str(status.get("style_color_input") or "").lower() == REQUESTED_COLOR,
        timeout=8.0,
        poll_name="style color preview dirty",
    )
    bounds_preview, pixel_preview = _wait_paint(client, expected_dominant="blue", timeout=10.0)
    post_preview_bytes = fixture_path.read_bytes()
    report["product_preview"] = {
        "ok": preview.get("ok") is True,
        "method": preview.get("method"),
        "color": preview.get("color"),
        "source_byte_identical": post_preview_bytes == pre_preview_bytes,
        "source_sha256": _sha256_bytes(post_preview_bytes),
        "pixel": pixel_preview,
        "bounds": bounds_preview,
        "save_enabled": preview_status.get("save_enabled"),
    }
    if not report["product_preview"]["source_byte_identical"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_preview_wrote_source"
        return report
    if pixel_preview.get("dominant") != "blue":
        report["verdict"] = "inconclusive"
        report["verdict_reason"] = "style_color_preview_paint_unproven"
        return report

    # Reset is local preview history, distinct from transactional product undo.
    reset = _require_ok(client.request("editor_task0_reset", {}), "style color preview reset")
    reset_status = _wait_for_status(
        client,
        lambda status: not bool(status.get("save_enabled"))
        and str(status.get("style_color_input") or "").lower() == BASELINE_COLOR,
        timeout=8.0,
        poll_name="style color preview reset clean",
    )
    reset_bounds, reset_pixel = _wait_paint(client, expected_dominant="red", timeout=10.0)
    reset_bytes = fixture_path.read_bytes()
    report["product_preview_reset"] = {
        "ok": reset.get("ok") is True
        and reset_bytes == baseline
        and reset_pixel.get("dominant") == "red",
        "source_byte_identical": reset_bytes == baseline,
        "pixel": reset_pixel,
        "bounds": reset_bounds,
        "save_enabled": reset_status.get("save_enabled"),
    }
    if not report["product_preview_reset"]["ok"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_preview_reset_failed"
        return report

    # Re-apply the requested preview before exercising commit rollback.
    _require_ok(
        client.request("editor_task0_style_color", {"color": REQUESTED_COLOR}),
        "style color preview after reset",
    )
    _wait_for_status(
        client,
        lambda status: bool(status.get("save_enabled"))
        and str(status.get("style_color_input") or "").lower() == REQUESTED_COLOR,
        timeout=8.0,
        poll_name="style color preview after reset dirty",
    )
    _wait_paint(client, expected_dominant="blue", timeout=10.0)

    # --- Deliberate refused attestation rollback ---
    _require_ok(
        client.request("editor_task0_force_style_attestation_refusal", {}),
        "force style attestation refusal",
    )
    refused_save = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "style color refused save",
    )
    refused_status = _wait_for_status(
        client,
        lambda status: (
            not bool(status.get("save_in_progress"))
            and status.get("status_code") in ("reload_failed", "commit_failed", "reload_handshake_failed")
        ),
        timeout=60.0,
        poll_name="style color refused attestation",
    )
    refused_bytes = fixture_path.read_bytes()
    report["refused_attestation_rollback"] = {
        "ok": refused_bytes == baseline,
        "byte_identical": refused_bytes == baseline,
        "sha256": _sha256_bytes(refused_bytes),
        "save_request": refused_save,
        "status_code": refused_status.get("status_code"), "status_text": refused_status.get("status_text"),
        "save_error": refused_status.get("save_error") or refused_status.get("save_last_error"),
    }
    if not report["refused_attestation_rollback"]["ok"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_refused_attestation_did_not_rollback"
        return report

    # Resync runtime to rolled-back source for the successful product pass.
    _require_ok(client.control("reload_script"), "post-refusal reload")
    for _ in range(40):
        if client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")') is True:
            break
        time.sleep(0.1)
    _show_fixture(client)
    bounds_red, pixel_red = _wait_paint(client, expected_dominant="red", timeout=10.0)
    report["post_refusal_paint"] = pixel_red
    report["post_refusal_bounds"] = bounds_red

    # Re-select and unlock again for the successful commit path.
    report["product_select_retry"] = _attempt_product_select(client, bounds_red)
    analysis_status = _wait_style_analysis(client, timeout=20.0)
    generation1 = _source_generation(analysis_status)
    _require_ok(
        client.request("editor_task0_style_color", {"color": REQUESTED_COLOR}),
        "style color preview before successful save",
    )
    _wait_for_status(
        client,
        lambda status: bool(status.get("save_enabled")),
        timeout=8.0,
        poll_name="style color ready to save",
    )
    bounds_preview2, pixel_preview2 = _wait_paint(client, expected_dominant="blue", timeout=10.0)
    report["product_preview_before_commit"] = {
        "pixel": pixel_preview2,
        "bounds": bounds_preview2,
        "source_byte_identical": fixture_path.read_bytes() == baseline,
    }

    pre_save_text = fixture_path.read_text(encoding="utf-8")
    save_request = _require_ok(
        client.click_element(id="rf_save", screen="_renforge_editor_overlay"),
        "style color product save",
    )
    save_status = _wait_for_status(
        client,
        lambda status: is_reload_committed(
            status,
            minimum_generation=generation1 + 1,
        ),
        timeout=60.0,
        poll_name="style color save complete",
    )
    post_save_bytes = fixture_path.read_bytes()
    post_save_text = post_save_bytes.decode("utf-8")
    expected_text = _independent_expected_after_color_patch(pre_save_text, color=REQUESTED_COLOR)
    outside_ok = _outside_color_span_identical(pre_save_text, post_save_text)
    report["source_patch"] = {
        "changed": post_save_text != pre_save_text,
        "matches_independent_expected": post_save_text == expected_text,
        "outside_color_span_identical": outside_ok,
        "source_color_after": _parse_color_from_target_line(post_save_text, TARGET_ID),
        "staged_sha256": _sha256_bytes(post_save_bytes),
        "save_request": save_request,
    }
    report["product_commit"] = {
        "ok": (
            report["source_patch"]["matches_independent_expected"]
            and report["source_patch"]["outside_color_span_identical"]
            and report["source_patch"]["source_color_after"] == REQUESTED_COLOR
            and is_reload_committed(save_status)
        ),
        "status_code": save_status.get("status_code"), "status_text": save_status.get("status_text"),
        "script_generation": _source_generation(save_status),
        "last_committed_transaction_id": save_status.get("last_committed_transaction_id"),
    }

    successor = _wait_for_status(
        client,
        lambda status: (
            bool(status.get("current_analysis_id"))
            and status.get("selected_widget_id") == TARGET_ID
            and status.get("selected_lock_reason") in (None, "")
            and (status.get("current_source_key") or {}).get("style_mode")
            == TEXT_STYLE_COLOR_MODE_LITERAL
            and (status.get("current_capabilities") or {}).get("style_color") is True
        ),
        timeout=15.0,
        poll_name="style color post-save rebind",
    )
    bounds_after, pixel_after = _wait_paint(client, expected_dominant="blue", timeout=10.0)
    report["pixel_after"] = pixel_after
    report["target_bounds_after"] = bounds_after
    report["published_source_after_reload"] = {
        "widget_id": TARGET_ID,
        "bounds_after": bounds_after,
        "source_color": _parse_color_from_target_line(post_save_text, TARGET_ID),
        "ok": _parse_color_from_target_line(post_save_text, TARGET_ID) == REQUESTED_COLOR,
    }
    report["rebinding"] = {
        "ok": (
            successor.get("selected_widget_id") == TARGET_ID
            and successor.get("current_analysis_id")
            not in (None, analysis_status.get("current_analysis_id"))
            and (successor.get("current_source_key") or {}).get("statement_kind") == "text"
            and (successor.get("current_source_key") or {}).get("style_mode")
            == TEXT_STYLE_COLOR_MODE_LITERAL
            and (successor.get("current_capabilities") or {}).get("style_color") is True
        ),
        "widget_id": successor.get("selected_widget_id"),
        "analysis_id": successor.get("current_analysis_id"),
        "previous_analysis_id": analysis_status.get("current_analysis_id"),
        "source_key": successor.get("current_source_key"),
        "capabilities": successor.get("current_capabilities"),
    }

    # --- Product undo ---
    generation2 = _source_generation(successor)
    undo_click = _require_ok(
        client.click_element(id="rf_undo", screen="_renforge_editor_overlay"),
        "style color product undo",
    )
    undo_status = _wait_for_status(
        client,
        lambda status: is_reload_committed(
            status,
            minimum_generation=generation2 + 1,
        ),
        timeout=60.0,
        poll_name="style color product undo complete",
    )
    undo_bytes = fixture_path.read_bytes()
    undo_text = undo_bytes.decode("utf-8")
    bounds_undo, pixel_undo = _wait_paint(client, expected_dominant="red", timeout=10.0)
    undo_rebind = _wait_for_status(
        client,
        lambda status: (
            bool(status.get("current_analysis_id"))
            and status.get("selected_widget_id") == TARGET_ID
            and status.get("selected_lock_reason") in (None, "")
            and (status.get("current_source_key") or {}).get("style_mode")
            == TEXT_STYLE_COLOR_MODE_LITERAL
            and (status.get("current_capabilities") or {}).get("style_color") is True
        ),
        timeout=15.0,
        poll_name="style color post-undo rebind",
    )
    report["product_undo"] = {
        "ok": (
            undo_bytes == baseline
            and _parse_color_from_target_line(undo_text, TARGET_ID) == BASELINE_COLOR
            and pixel_undo.get("dominant") == "red"
            and undo_rebind.get("selected_widget_id") == TARGET_ID
            and is_reload_committed(undo_status)
        ),
        "byte_identical": undo_bytes == baseline,
        "sha256": _sha256_bytes(undo_bytes),
        "source_color": _parse_color_from_target_line(undo_text, TARGET_ID),
        "pixel": pixel_undo,
        "bounds": bounds_undo,
        "click": undo_click,
        "status_code": undo_status.get("status_code"), "status_text": undo_status.get("status_text"),
        "rebinding": {
            "widget_id": undo_rebind.get("selected_widget_id"),
            "analysis_id": undo_rebind.get("current_analysis_id"),
            "capabilities": undo_rebind.get("current_capabilities"),
        },
        "note": "product_undo_transaction",
    }

    # Manual fixture restore is cleanup only if product undo already restored bytes.
    restored = fixture_path.read_bytes()
    if restored != baseline:
        fixture_path.write_bytes(baseline)
        restored = fixture_path.read_bytes()
        report["restore"] = {
            "sha256": _sha256_bytes(restored),
            "byte_identical": restored == baseline,
            "note": "manual_fixture_restore_cleanup_not_product_undo",
        }
    else:
        report["restore"] = {
            "sha256": _sha256_bytes(restored),
            "byte_identical": True,
            "note": "product_undo_restored_baseline",
        }

    report["runtime_color_change_proven"] = (
        pixel_before.get("dominant") == "red"
        and pixel_after.get("dominant") == "blue"
        and pixel_before.get("bounds_from_scene_tree") is True
        and pixel_after.get("bounds_from_scene_tree") is True
        and int(pixel_before.get("paint_count") or 0) > 20
        and int(pixel_after.get("paint_count") or 0) > 20
    )

    # Visible-control proof: toolbar/style buttons drive preview colour without
    # writing source (cycle toggles baseline ↔ proof blue).
    _require_ok(
        client.click_element(id="rf_style_color", screen="_renforge_editor_overlay"),
        "rf_style_color click",
    )
    _, style_button_paint = _wait_paint(client, expected_dominant="blue", timeout=10.0)
    style_button_status = client.request("editor_task0_status")
    _require_ok(
        client.click_element(id="rf_style_cycle", screen="_renforge_editor_overlay"),
        "rf_style_cycle click",
    )
    _, style_cycle_paint = _wait_paint(client, expected_dominant="red", timeout=10.0)
    style_cycle_status = client.request("editor_task0_status")
    style_source_untouched = fixture_path.read_bytes() == baseline
    report["style_button_clicks"] = {
        "ok": (
            style_button_paint.get("dominant") == "blue"
            and style_button_status.get("style_color_input") == REQUESTED_COLOR
            and bool(style_button_status.get("save_enabled")) is True
            and style_cycle_paint.get("dominant") == "red"
            and style_cycle_status.get("style_color_input") == BASELINE_COLOR
            and bool(style_cycle_status.get("save_enabled")) is False
            and style_source_untouched
        ),
        "rf_style_color": {
            "pixel": style_button_paint,
            "style_color_input": style_button_status.get("style_color_input"),
            "save_enabled": style_button_status.get("save_enabled"),
            "dirty_target_count": style_button_status.get("dirty_target_count"),
        },
        "rf_style_cycle": {
            "pixel": style_cycle_paint,
            "style_color_input": style_cycle_status.get("style_color_input"),
            "save_enabled": style_cycle_status.get("save_enabled"),
            "dirty_target_count": style_cycle_status.get("dirty_target_count"),
        },
        "source_byte_identical": style_source_untouched,
    }

    required = [
        report["product_select_unlocked_style"],
        report["product_preview"]["ok"] and report["product_preview"]["source_byte_identical"],
        report["product_preview"]["pixel"].get("dominant") == "blue",
        report["product_preview_reset"]["ok"],
        report["refused_attestation_rollback"]["ok"],
        report["product_commit"]["ok"],
        report["runtime_color_change_proven"],
        report["rebinding"]["ok"],
        report["product_undo"]["ok"],
        report["locks"]["inherited"]["matches_expected"],
        report["locks"]["expression"]["matches_expected"],
        report["runtime_alpha"]["ok"],
        report["runtime_repeated_lock"]["ok"],
        report["restore"]["byte_identical"],
        report["style_button_clicks"]["ok"],
    ]
    if all(required):
        report["verdict"] = "pass"
        report["verdict_reason"] = None
    elif not report["product_select_unlocked_style"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_path_missing"
    elif not report["refused_attestation_rollback"]["ok"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_refused_attestation_did_not_rollback"
    elif not report["product_commit"]["ok"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_commit_failed"
    elif not report["product_undo"]["ok"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_undo_failed"
    elif not report["runtime_color_change_proven"]:
        report["verdict"] = "inconclusive"
        report["verdict_reason"] = "pixel_color_change_unproven"
    elif not report["style_button_clicks"]["ok"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_visible_controls_unwired"
    else:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_evidence_incomplete"

    # Keep generation0 referenced for diagnostics.
    report["generations"] = {
        "initial_analysis": generation0,
        "pre_commit": generation1,
        "post_commit": _source_generation(save_status),
        "pre_undo": generation2,
        "post_undo": _source_generation(undo_status),
    }
    return report
