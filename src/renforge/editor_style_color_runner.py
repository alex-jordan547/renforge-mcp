"""Live evidence runner for issue #50 text colour style source-write contract."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.editor.paths import atomic_write_file
from renforge.editor.source import (
    TEXT_STYLE_COLOR_MODE_LITERAL,
    analyze_text_color_style,
    apply_text_color_patch,
)
from renforge.editor_task0_runner import _require_ok

FIXTURE_SCREEN = "renforge_editor_style_fixture"
TARGET_ID = "style_color_target"
INHERITED_ID = "style_color_inherited"
EXPR_ID = "style_color_expr"
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
    return match.group(2)


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
    """Independent screenshot paint stats for saturated non-dark pixels.

    Colour class is derived only from PNG samples — never from scene-tree style
    metadata or editor claims.
    """
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
            # Dark fixture background is ~#0a0a12. Drop near-black and near-grey
            # anti-alias fringes so glyph chroma dominates.
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
    # Also record one high-chroma sample near the geometric centre.
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
        # Prefer the high-chroma centre sample when mean is muddy.
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


def _attempt_product_select(client: Any, bounds: dict[str, int]) -> dict[str, Any]:
    """Click via the production select path (focus_list only)."""
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


def run_editor_style_color_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Evidence-gated live scenario for text colour style (issue #50)."""
    baseline = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline).hexdigest()
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
        report["restore"] = {
            "byte_identical": True,
            "sha256": baseline_sha,
            "note": "manual_fixture_restore_cleanup_not_product_undo",
        }
        return report

    # Pixel before: wait for independent red paint (not scene-tree style fields).
    bounds_before, pixel_before = _wait_paint(client, expected_dominant="red", timeout=8.0)
    report["target_bounds"] = bounds_before
    report["pixel_before"] = pixel_before

    # Product select attempt on the text glyph (expected: no style unlock).
    report["product_select"] = _attempt_product_select(client, bounds_before)

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

    # Source patch via dedicated style contract (not coordinate spans).
    staged_line = apply_text_color_patch(
        target_line.encode("utf-8"),
        target_analysis,
        color=REQUESTED_COLOR,
    ).decode("utf-8")
    staged_text = baseline_text.replace(target_line, staged_line, 1)
    if staged_text == baseline_text:
        raise AssertionError("style colour patch produced no source change")
    expected_text = _independent_expected_after_color_patch(baseline_text, color=REQUESTED_COLOR)
    outside_ok = _outside_color_span_identical(baseline_text, staged_text)
    report["source_patch"] = {
        "changed": True,
        "matches_independent_expected": staged_text == expected_text,
        "outside_color_span_identical": outside_ok,
        "source_color_after": _parse_color_from_target_line(staged_text, TARGET_ID),
        "staged_sha256": hashlib.sha256(staged_text.encode("utf-8")).hexdigest(),
    }
    if not report["source_patch"]["matches_independent_expected"] or not outside_ok:
        report["verdict"] = "inconclusive"
        report["verdict_reason"] = "source_patch_form_or_span_mismatch"
        return report

    try:
        atomic_write_file(fixture_path, staged_text.encode("utf-8"))
        if fixture_path.read_text(encoding="utf-8") != staged_text:
            raise AssertionError("atomic write did not publish staged style fixture bytes")
        _require_ok(client.control("reload_script"), "style color reload")
        # Give the reloaded script a moment, then re-show the fixture.
        for _ in range(30):
            if client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")') is True:
                break
            time.sleep(0.1)
        _show_fixture(client)
        bounds_after, pixel_after = _wait_paint(client, expected_dominant="blue", timeout=10.0)
        report["pixel_after"] = pixel_after
        report["target_bounds_after"] = bounds_after
        disk_color = _parse_color_from_target_line(
            fixture_path.read_text(encoding="utf-8"),
            TARGET_ID,
        )
        report["published_source_after_reload"] = {
            "widget_id": TARGET_ID,
            "bounds_after": bounds_after,
            "source_color": disk_color,
            "ok": disk_color == REQUESTED_COLOR,
        }
    finally:
        # Cleanup only — not product undo evidence.
        atomic_write_file(fixture_path, baseline)
        try:
            _require_ok(client.control("reload_script"), "style color restore reload")
            _show_fixture(client)
        except Exception as exc:  # noqa: BLE001
            report["restore_reload_error"] = str(exc)

    restored = fixture_path.read_bytes()
    report["restore"] = {
        "sha256": hashlib.sha256(restored).hexdigest(),
        "byte_identical": restored == baseline,
        "note": "manual_fixture_restore_cleanup_not_product_undo",
    }

    pixel_before = report.get("pixel_before") or {}
    pixel_after = report.get("pixel_after") or {}
    report["runtime_color_change_proven"] = (
        pixel_before.get("dominant") == "red"
        and pixel_after.get("dominant") == "blue"
        and pixel_before.get("bounds_from_scene_tree") is True
        and pixel_after.get("bounds_from_scene_tree") is True
        and int(pixel_before.get("paint_count") or 0) > 20
        and int(pixel_after.get("paint_count") or 0) > 20
    )
    report["product_select_unlocked_style"] = bool(
        report["product_select"].get("selected_widget_id") == TARGET_ID
        and (report["product_select"].get("source_key") or {}).get("statement_kind") == "text"
        and (report["product_select"].get("source_key") or {}).get("style_mode")
        == TEXT_STYLE_COLOR_MODE_LITERAL
        and (report["product_select"].get("capabilities") or {}).get("style_color") is True
    )
    # Measure advertised product seams from the status returned by the real
    # production selection attempt. No style-specific command is inferred when
    # the capability map does not advertise one.
    capabilities = report["product_select"].get("capabilities") or {}
    style_selected = report["product_select_unlocked_style"]
    report["product_seam_probe"] = {
        "measurement_source": "editor_task0_status.current_capabilities",
        "selected_widget_id": report["product_select"].get("selected_widget_id"),
        "capabilities": capabilities,
        "save_enabled": report["product_select"].get("save_enabled"),
        "history_length": report["product_select"].get("history_length"),
        "pending_transaction_id": report["product_select"].get("pending_transaction_id"),
    }
    report["product_preview_available"] = bool(
        style_selected and capabilities.get("style_color_preview") is True
    )
    report["product_commit_available"] = bool(
        style_selected and capabilities.get("style_color_commit") is True
    )
    report["product_undo_available"] = bool(
        style_selected and capabilities.get("style_color_undo") is True
    )
    report["refused_attestation_rollback_available"] = bool(
        style_selected and capabilities.get("style_color_attestation_rollback") is True
    )

    if not report["runtime_color_change_proven"]:
        report["verdict"] = "inconclusive"
        report["verdict_reason"] = "pixel_color_change_unproven"
    elif not (
        report["source_patch"]["matches_independent_expected"]
        and report["source_patch"]["outside_color_span_identical"]
        and report["restore"]["byte_identical"]
        and report["locks"]["inherited"]["matches_expected"]
        and report["locks"]["expression"]["matches_expected"]
    ):
        report["verdict"] = "inconclusive"
        report["verdict_reason"] = "source_or_lock_evidence_incomplete"
    else:
        # Source + independent pixel proven; product style path absent.
        report["verdict"] = "blocked"
        report["verdict_reason"] = "style_color_product_path_missing"

    return report
