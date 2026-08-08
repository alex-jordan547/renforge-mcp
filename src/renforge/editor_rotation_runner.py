"""Live spike for issue #48 — rotated Transform geometry + write proof."""

from __future__ import annotations

import hashlib
import io
import math
import re
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.editor_live_common import (
    center as _center,
    observe_selected as _observe_selected,
    sha256_file as _sha256_file,
    wait_bounds,
)
from renforge.editor_task0_runner import (
    _require_ok,
    _source_generation,
    _wait_for_status,
)

FIXTURE_SCREEN = "renforge_editor_rotation_fixture"
TARGET_ID = "rotation_target"
REFERENCE_ID = "rotation_reference"
EXTRA_ID = "rotation_other"
ROTATION_IDS = (TARGET_ID, REFERENCE_ID, EXTRA_ID)

SPIKE_RESOURCE = Path(__file__).resolve().parent / "bridge" / "rotation_spike.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_rotation_fixture.rpy"
)

BG_LUMA_MAX = 28

# Visible Move offsets (screen px) used to carry each widget outside its own
# rect while capturing isolation baselines. Destinations stay inside the
# 1280x720 stage and clear every fixture home rect.
ISOLATION_MOVE_DELTAS = {
    TARGET_ID: (400, 240),
    REFERENCE_ID: (200, 280),
    EXTRA_ID: (420, -170),
}


def inject_editor_rotation_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_rotation.rpy"
    fixture_target = game_dir / "zz_renforge_editor_rotation_fixture.rpy"
    shutil.copyfile(SPIKE_RESOURCE, editor_target)
    shutil.copyfile(FIXTURE_RESOURCE, fixture_target)
    return {"editor": str(editor_target), "fixture": str(fixture_target)}


def _parse_xy(source_text: str, *, widget_id: str) -> dict[str, int]:
    def resolve_literal(token: str | None, fallback: str) -> int:
        if token is None:
            raise AssertionError(f"{widget_id} source missing positional token {fallback}: {source_text!r}")
        stripped = token.strip()
        numeric = re.fullmatch(r"-?\d+", stripped)
        if numeric is not None:
            return int(stripped)
        default_match = re.search(
            rf"^\s*default\s+{re.escape(stripped)}\s*=\s*(-?\d+)\s*$",
            source_text,
            flags=re.MULTILINE,
        )
        if default_match is None:
            raise AssertionError(f"{widget_id} unresolved positional token {token!r} for {fallback}")
        return int(default_match.group(1))

    pattern = rf"(?:textbutton|button)[\s\S]{{0,280}}id \"{re.escape(widget_id)}\"[\s\S]{{0,280}}"
    match = re.search(pattern, source_text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"source missing widget block for {widget_id!r}: {widget_id!r}")
    block = match.group(0)
    xpos = re.search(r"\bxpos\s+([A-Za-z_][A-Za-z0-9_]*|-?\d+)\b", block)
    ypos = re.search(r"\bypos\s+([A-Za-z_][A-Za-z0-9_]*|-?\d+)\b", block)
    if xpos is None or ypos is None:
        raise AssertionError(f"{widget_id} source block missing xpos/ypos: {block!r}")
    return {
        "x": resolve_literal(xpos.group(1), "xpos"),
        "y": resolve_literal(ypos.group(1), "ypos"),
    }


def _extract_rotate_span(source_text: str) -> tuple[int, int, int]:
    block_pattern = rf'(?:textbutton|button)[\s\S]*?id "{re.escape(TARGET_ID)}"[\s\S]*?(?=\n\s*#|\n\s*$|$)'
    match = re.search(block_pattern, source_text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"rotated target block missing rotate literal")
    block = match.group(0)
    block_start = match.start(0)
    rotate_match = re.search(r"rotate\s*=\s*(-?\d+)", block)
    if rotate_match is None:
        raise AssertionError(f"rotated target block missing rotate literal")
    value = int(rotate_match.group(1))
    return value, block_start + rotate_match.start(1), block_start + rotate_match.end(1)


def _patch_rotate_literal(source_text: str, new_value: int) -> tuple[str, int, int]:
    _, start_char, end_char = _extract_rotate_span(source_text)
    start = len(source_text[:start_char].encode("utf-8"))
    end = len(source_text[:end_char].encode("utf-8"))
    before_bytes = source_text.encode("utf-8")
    patched = before_bytes[:start] + str(int(new_value)).encode("utf-8") + before_bytes[end:]
    return patched.decode("utf-8"), start, end


def _independent_expected_position_patch(
    source_text: str,
    *,
    x: int,
    y: int,
) -> str:
    pattern = re.compile(
        rf'(^\s*button\b[^\n]*\bid "{re.escape(TARGET_ID)}"[^\n]*\bxpos\s+)-?\d+'
        r'([^\n]*\bypos\s+)-?\d+',
        flags=re.MULTILINE,
    )
    patched, count = pattern.subn(
        rf'\g<1>{int(x)}\g<2>{int(y)}',
        source_text,
        count=1,
    )
    if count != 1 or patched == source_text:
        raise AssertionError("independent target position patch did not change the fixture")
    return patched


def _sample_rgb(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    width, height = image.size
    if not (0 <= x < width and 0 <= y < height):
        return (0, 0, 0)
    pixel = image.getpixel((x, y))
    if isinstance(pixel, int):
        return (int(pixel), int(pixel), int(pixel))
    if len(pixel) >= 3:
        return (int(pixel[0]), int(pixel[1]), int(pixel[2]))
    return (0, 0, 0)


def _is_paint_pixel(rgb: tuple[int, int, int]) -> bool:
    return max(rgb) > BG_LUMA_MAX


def _build_isolation_mask(image: Image.Image, roi: list[int] | None = None) -> set[tuple[int, int]]:
    width, height = image.size
    if roi and len(roi) == 4:
        x0 = max(0, int(roi[0]))
        y0 = max(0, int(roi[1]))
        x1 = min(width, int(roi[0] + roi[2]))
        y1 = min(height, int(roi[1] + roi[3]))
    else:
        x0, y0, x1, y1 = 0, 0, width, height

    painted: set[tuple[int, int]] = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if _is_paint_pixel(_sample_rgb(image, x, y)):
                painted.add((x, y))
    return painted


def _mask_contains(mask: set[tuple[int, int]], x: int, y: int, *, radius: int = 1) -> bool:
    if radius <= 0:
        return (x, y) in mask
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if (x + dx, y + dy) in mask:
                return True
    return False


def _edge_probe_from_quad(quad: Any) -> list[int] | None:
    if not isinstance(quad, list) or len(quad) != 4:
        return None
    try:
        points = [(float(point[0]), float(point[1])) for point in quad]
    except (TypeError, ValueError, IndexError):
        return None
    center = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    p1, p2 = max(
        zip(points, points[1:] + points[:1]),
        key=lambda edge: (edge[1][0] - edge[0][0]) ** 2 + (edge[1][1] - edge[0][1]) ** 2,
    )
    midpoint = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    toward_center = (center[0] - midpoint[0], center[1] - midpoint[1])
    distance = math.hypot(*toward_center)
    if distance <= 0.0:
        return None
    inset = 2.0
    return [
        int(round(midpoint[0] + toward_center[0] * inset / distance)),
        int(round(midpoint[1] + toward_center[1] * inset / distance)),
    ]


def _sample_mask_points(
    mask: set[tuple[int, int]],
    aabb: list[int],
    *,
    edge_probe: list[int] | None = None,
) -> dict[str, Any]:
    if not aabb or len(aabb) != 4:
        return {"center": {}, "edge": {}, "aabb_corner": {}}

    x, y, w, h = [int(v) for v in aabb]
    if w <= 0 or h <= 0:
        return {"center": {}, "edge": {}, "aabb_corner": {}}

    cx = x + w // 2
    cy = y + h // 2
    center = {
        "point": [cx, cy],
        "painted": _mask_contains(mask, cx, cy, radius=1),
    }

    corner = (x, y)
    return {
        "center": center,
        "edge": {
            "point": edge_probe,
            "painted": bool(
                edge_probe is not None
                and _mask_contains(mask, edge_probe[0], edge_probe[1], radius=1)
            ),
            "source": "runtime_transform_quad_inset",
        },
        "aabb_corner": {
            "point": [corner[0], corner[1]],
            "painted": _mask_contains(mask, corner[0], corner[1], radius=1),
        },
    }


def _measure_geometry(client: Any, *, target_ids: list[str] | None = None) -> dict[str, Any]:
    reply = client.request("rotation_spike_measure", {"target_ids": target_ids or []})
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise AssertionError(f"rotation_spike_measure failed: {reply!r}")
    geometry = reply.get("geometry")
    if not isinstance(geometry, dict):
        raise AssertionError(f"rotation_spike_measure malformed: {reply!r}")
    return geometry


def _rects_overlap(a: list[int], b: list[int]) -> bool:
    return not (
        a[0] + a[2] <= b[0]
        or b[0] + b[2] <= a[0]
        or a[1] + a[3] <= b[1]
        or b[1] + b[3] <= a[1]
    )


def _set_view_mode(client: Any, mode: str) -> None:
    """Toggle edit/preview through the visible toolbar segment."""
    button_id = "rf_toolbar_view_preview" if mode == "preview" else "rf_toolbar_view_edit"
    reply = client.click_element(id=button_id, screen="_renforge_editor_overlay")
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise AssertionError(f"{button_id} click failed: {reply!r}")
    deadline = time.monotonic() + 8.0
    last: Any = None
    while time.monotonic() < deadline:
        last = client.eval_expr("_renforge_editor_view_mode()")
        if last == mode:
            return
        time.sleep(0.05)
    raise AssertionError(f"view mode never became {mode!r}: last={last!r}")


def _settled_screenshot(client: Any) -> bytes:
    """Screenshot once the frame stops changing (mode switches can animate)."""
    previous = client.screenshot()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        time.sleep(0.12)
        current = client.screenshot()
        if current == previous:
            return current
        previous = current
    return previous


def _capture_preview_frame(client: Any) -> Image.Image:
    """One chrome-free frame via the visible Preview path, restored to Edit."""
    _set_view_mode(client, "preview")
    png = _settled_screenshot(client)
    _set_view_mode(client, "edit")
    return Image.open(io.BytesIO(png)).convert("RGB")


def _isolate_via_preview(
    client: Any,
    *,
    fixture_path: Path,
    focus_aabb: dict[str, list[int]],
    transform_plane: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Isolate each widget's paint using only visible paths.

    A Preview-mode frame shows the fixture without editor chrome. To prove
    which pixels belong to one widget, carry that widget outside its own rect
    with a visible Move gesture, capture the baseline frame, restore it with a
    visible Undo, assert the source bytes never changed, and subtract the
    baseline from the full frame before sampling the mask.
    """
    source_before = fixture_path.read_bytes()
    full_image = _capture_preview_frame(client)

    isolation: dict[str, Any] = {}
    protocol: dict[str, Any] = {}
    for wid in ROTATION_IDS:
        aabb = list(focus_aabb[wid])
        cx = aabb[0] + aabb[2] // 2
        cy = aabb[1] + aabb[3] // 2
        delta_x, delta_y = ISOLATION_MOVE_DELTAS[wid]

        select = _require_ok(
            client.request("editor_task0_select", {"x": cx, "y": cy}),
            f"{wid} isolation select",
        )
        if (select.get("selected") or {}).get("widget_id") != wid:
            raise AssertionError(f"isolation select for {wid} hit another widget: {select!r}")
        lock = select.get("lock_reason")
        if isinstance(lock, str) and lock and lock != "ANALYZING":
            raise AssertionError(f"isolation select for {wid} is locked: {select!r}")

        status_before = _wait_for_status(
            client,
            lambda status: status.get("selected_widget_id") == wid
            and status.get("selected_lock_reason") != "ANALYZING"
            and status.get("selected_analysis_pending") is not True,
            timeout=10.0,
            poll_name=f"{wid} isolation settle",
        )
        settled_lock = status_before.get("selected_lock_reason")
        if isinstance(settled_lock, str) and settled_lock:
            raise AssertionError(f"isolation target {wid} is locked: {settled_lock!r}")
        home_raw = status_before.get("preview_position") or status_before.get("selected_original_position")
        if not (isinstance(home_raw, (list, tuple)) and len(home_raw) == 2):
            raise AssertionError(f"{wid} isolation has no home position: {status_before!r}")
        home = [int(home_raw[0]), int(home_raw[1])]

        _require_ok(
            client.request(
                "editor_task0_drag",
                {
                    "points": [[cx, cy], [cx + delta_x, cy + delta_y]],
                    "coordinate_space": "screen",
                },
            ),
            f"{wid} isolation move",
        )
        moved_status = _wait_for_status(
            client,
            lambda status: isinstance(status.get("preview_position"), (list, tuple))
            and [int(v) for v in status.get("preview_position")] != home,
            timeout=8.0,
            poll_name=f"{wid} isolation move",
        )
        moved = [int(v) for v in moved_status["preview_position"]]
        actual_delta = [moved[0] - home[0], moved[1] - home[1]]
        moved_rect = [aabb[0] + actual_delta[0], aabb[1] + actual_delta[1], aabb[2], aabb[3]]
        if _rects_overlap(moved_rect, aabb):
            raise AssertionError(
                f"{wid} isolation move did not leave its rect: moved {moved_rect!r} overlaps home {aabb!r}"
            )

        baseline_image = _capture_preview_frame(client)

        _require_ok(
            client.click_element(id="rf_undo", screen="_renforge_editor_overlay"),
            f"{wid} isolation undo",
        )
        _wait_for_status(
            client,
            lambda status: list(status.get("preview_position") or []) == home,
            timeout=8.0,
            poll_name=f"{wid} isolation undo",
        )
        if fixture_path.read_bytes() != source_before:
            raise AssertionError(f"{wid} isolation changed source bytes")

        full_mask = _build_isolation_mask(full_image, roi=aabb)
        baseline_mask = _build_isolation_mask(baseline_image, roi=aabb)
        mask = full_mask - baseline_mask
        edge_probe = (
            _edge_probe_from_quad((transform_plane.get(wid) or {}).get("quad"))
            if wid == TARGET_ID
            else None
        )
        isolation[wid] = {
            "target_id": wid,
            "painted_pixels": len(mask),
            "point_samples": _sample_mask_points(mask, aabb, edge_probe=edge_probe),
        }
        protocol[wid] = {
            "home": home,
            "requested_delta": [delta_x, delta_y],
            "actual_delta": actual_delta,
            "moved_rect": moved_rect,
            "full_pixels_in_rect": len(full_mask),
            "baseline_pixels_in_rect": len(baseline_mask),
        }
    return isolation, protocol


def _attempt_save_and_rebind(
    client: Any,
    *,
    fixture_path: Path,
    expected_move: list[int],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "reason": None,
        "status_text": None,
    }

    pre_analysis = _require_ok(
        client.request("editor_task0_status"),
        "rotation status before save",
    )
    generation_before = _source_generation(pre_analysis)

    pre_text = fixture_path.read_text(encoding="utf-8")
    pre_pos = _parse_xy(pre_text, widget_id=TARGET_ID)
    expected_pos = {
        "x": pre_pos["x"] + int(expected_move[0]),
        "y": pre_pos["y"] + int(expected_move[1]),
    }
    expected_text = _independent_expected_position_patch(
        pre_text,
        x=expected_pos["x"],
        y=expected_pos["y"],
    )

    save_request = client.click_element(id="rf_save", screen="_renforge_editor_overlay")
    if not isinstance(save_request, dict) or save_request.get("ok") is not True:
        return {
            "ok": False,
            "reason": "write_chain_failed",
            "save_request": save_request,
        }

    save_status = _wait_for_status(
        client,
        lambda status: (not bool(status.get("save_in_progress")))
        and str(status.get("status_text")) in {"Reload committed", "Reload failed"},
        timeout=60.0,
        poll_name="rotation save settle",
    )
    report["status_text"] = str(save_status.get("status_text"))
    if report["status_text"] != "Reload committed":
        return {
            "ok": False,
            "reason": "write_chain_failed",
            "status_text": report["status_text"],
            "save_error": save_status.get("save_error"),
        }

    generation_after = _source_generation(save_status)
    if isinstance(generation_before, int) and generation_after != generation_before + 1:
        return {
            "ok": False,
            "reason": "write_chain_failed",
            "status_text": report["status_text"],
            "generation_before": generation_before,
            "generation_after": generation_after,
        }

    post_status = _wait_for_status(
        client,
        lambda status: status.get("selected_widget_id") == TARGET_ID
        and status.get("selected_lock_reason") != "ANALYZING"
        and status.get("selected_analysis_pending") is not True,
        timeout=10.0,
        poll_name="rotation post-save rebind",
    )
    selected_reason = post_status.get("selected_lock_reason")
    if selected_reason not in (None, ""):
        return {
            "ok": False,
            "reason": "write_chain_failed",
            "status_text": report["status_text"],
            "post_save_rebind_lock_reason": selected_reason,
        }

    post_text = fixture_path.read_text(encoding="utf-8")
    post_pos = _parse_xy(post_text, widget_id=TARGET_ID)
    source_preserved = post_text == expected_text
    if not source_preserved or post_pos != expected_pos:
        return {
            "ok": False,
            "reason": "source_preservation_failed",
            "expected_position": expected_pos,
            "post_position": post_pos,
            "matches_independent_expected": source_preserved,
        }

    report.update(
        {
            "ok": True,
            "generation_before": generation_before,
            "generation_after": generation_after,
            "generation_delta": generation_after - generation_before if isinstance(generation_before, int) else None,
            "save_request": save_request,
            "expected_position": expected_pos,
            "post_position": post_pos,
            "outside_bytes_equal": source_preserved,
            "matches_independent_expected": source_preserved,
            "post_save_rebind_lock_reason": selected_reason,
            "selected_widget": post_status.get("selected_widget_id"),
            "status_text": report["status_text"],
            "preview_to_source_delta": [expected_pos["x"] - pre_pos["x"], expected_pos["y"] - pre_pos["y"]],
        }
    )
    return report


def _run_manual_rotate_roundtrip(fixture_path: Path) -> dict[str, Any]:
    before = fixture_path.read_bytes()
    before_text = before.decode("utf-8")
    original_value, start, end = _extract_rotate_span(before_text)
    patched_value = original_value + 1

    patched_text, patch_start, patch_end = _patch_rotate_literal(before_text, patched_value)
    patched_bytes = patched_text.encode("utf-8")
    patched_end = patch_start + len(str(patched_value).encode("utf-8"))
    outside_equal = (
        before[:patch_start] == patched_bytes[:patch_start]
        and before[patch_end:] == patched_bytes[patched_end:]
    )
    patch_sha = hashlib.sha256(patched_bytes).hexdigest()

    fixture_path.write_bytes(patched_bytes)
    fixture_path.write_bytes(before)
    restored = fixture_path.read_bytes()

    return {
        "rotate": {
            "before": original_value,
            "patched": patched_value,
        },
        "outside_bytes_equal": outside_equal,
        "patch": {
            "start": patch_start,
            "original_end": patch_end,
            "patched_end": patched_end,
        },
        "patched_sha256": patch_sha,
        "restored_sha256": hashlib.sha256(restored).hexdigest(),
        "matches_baseline": restored == before,
    }


def run_editor_rotation_live_scenario(
    client: Any,
    *,
    fixture_path: Path,
) -> dict[str, Any]:
    """TDD live spike for issue #48."""
    report: dict[str, Any] = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_text = baseline_bytes.decode("utf-8")
    baseline_sha = _sha256_file(fixture_path)
    baseline_position = _parse_xy(baseline_text, widget_id=TARGET_ID)
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "position": baseline_position,
    }

    start_reply = _require_ok(
        client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}),
        "editor_task0_start",
    )
    report["start"] = {
        "ok": True,
        "status_text": start_reply.get("status_text"),
    }

    # Separate focus plane evidence (AABB from list_ui / runtime focus list).
    focus_aabb: dict[str, Any] = {}
    for wid in ROTATION_IDS:
        bounds = wait_bounds(client, wid, fixture_screen=FIXTURE_SCREEN)
        focus_aabb[wid] = {"aabb": [bounds["x"], bounds["y"], bounds["width"], bounds["height"]]}
    report["focus_aabb"] = focus_aabb

    # Runtime seams evidence from temporary bridge handler.
    geometry = _measure_geometry(client, target_ids=list(ROTATION_IDS))
    transform_plane: dict[str, Any] = {}
    alias = {
        TARGET_ID: "rotated",
        REFERENCE_ID: "reference",
        EXTRA_ID: "other",
    }
    for widget_id, payload in geometry.items():
        key = alias.get(widget_id, widget_id)
        transform_plane[key] = {
            "found": bool(payload.get("found")),
            "transform_present": bool(payload.get("transform_present")),
            "quad_available": bool(payload.get("quad")),
            "quad": payload.get("quad"),
            "notes": str(payload.get("notes", "")),
            "roundtrip_error": payload.get("roundtrip_error"),
            "quad_source": payload.get("quad_source"),
            "quad_coordinate_space": payload.get("quad_coordinate_space"),
        }
        # Keep raw ids available for easier debugging.
        transform_plane[widget_id] = transform_plane[key]
    report["transform_plane"] = transform_plane

    # Candidate-isolated paint mask evidence through visible paths only:
    # Preview-mode frames (chrome hidden) and baseline subtraction after a
    # visible Move gesture carries each widget outside its own rect.
    isolation_results, isolation_protocol = _isolate_via_preview(
        client,
        fixture_path=fixture_path,
        focus_aabb={wid: focus_aabb[wid]["aabb"] for wid in ROTATION_IDS},
        transform_plane=transform_plane,
    )
    report["isolation"] = {}
    for wid in ROTATION_IDS:
        alias_key = alias.get(wid, wid)
        result = isolation_results[wid]
        report["isolation"][alias_key] = {
            **result,
            "aabb_corner_painted": result["point_samples"]["aabb_corner"]["painted"],
        }
        report["isolation"][wid] = report["isolation"][alias_key]
    report["isolation_protocol"] = isolation_protocol

    # Probe the unpainted AABB corner through the real editor selection path.
    corner_sample = report["isolation"]["rotated"]["point_samples"]["aabb_corner"]
    corner_point = corner_sample.get("point")
    if not (isinstance(corner_point, list) and len(corner_point) == 2):
        raise AssertionError(f"rotated AABB corner probe is unavailable: {corner_sample!r}")
    corner_select = client.request("editor_task0_select", {"x": corner_point[0], "y": corner_point[1]})
    corner_selected = (corner_select.get("selected") or {}).get("widget_id")
    report["aabb_corner_probe"] = {
        "point": corner_point,
        "painted": bool(corner_sample.get("painted")),
        "selected_widget_id": corner_selected,
        "selected_rotated": corner_selected == TARGET_ID,
    }

    # Resolve and move the rotated control from a painted center point.
    target_bounds = focus_aabb[TARGET_ID]["aabb"]
    target_center = _center({"x": target_bounds[0], "y": target_bounds[1], "width": target_bounds[2], "height": target_bounds[3]})

    select = _require_ok(
        client.request("editor_task0_select", {"x": target_center[0], "y": target_center[1]}),
        "rotation target select",
    )
    lock_reason = select.get("lock_reason")
    verdict: str
    if isinstance(lock_reason, str) and lock_reason and lock_reason != "ANALYZING":
        report["write_chain"] = {
            "ok": False,
            "reason": "selection_locked",
            "selection_lock_reason": lock_reason,
        }
        verdict = "blocked"
        report["verdict_reason"] = "selection_locked"
    else:
        analysis_status = _wait_for_status(
            client,
            lambda status: status.get("selected_widget_id") == TARGET_ID
            and status.get("selected_lock_reason") != "ANALYZING"
            and status.get("selected_analysis_pending") is not True,
            timeout=10.0,
            poll_name="rotation analysis",
        )
        analysis_lock = analysis_status.get("selected_lock_reason")
        if isinstance(analysis_lock, str) and analysis_lock:
            report["write_chain"] = {
                "ok": False,
                "reason": "selection_locked",
                "selection_lock_reason": analysis_lock,
            }
            verdict = "blocked"
            report["verdict_reason"] = "selection_locked"
            report["write_chain"].update(
                {
                "selected_widget_id": analysis_status.get("selected_widget_id"),
                "selected_lock_reason": analysis_lock,
                }
            )
        else:
            original = analysis_status.get("selected_original_position")
            if not (isinstance(original, (list, tuple)) and len(original) == 2):
                original = [baseline_position["x"], baseline_position["y"]]
            requested_before = [int(original[0]), int(original[1])]

            pre_obs = _observe_selected(client)
            pre_rect = pre_obs.get("rect") or []

            _require_ok(
                client.request("editor_task0_key", {"key": "right", "repeat": 1}),
                "rotation nudge right",
            )
            preview_status = _wait_for_status(
                client,
                lambda status: isinstance(status.get("preview_position"), (list, tuple))
                and len(status.get("preview_position") or []) == 2
                and list(status.get("preview_position") or []) != requested_before,
                timeout=8.0,
                poll_name="rotation preview",
            )

            preview_after = [int(preview_status["preview_position"][0]), int(preview_status["preview_position"][1])]
            requested_after = preview_after
            requested_move = [
                requested_after[0] - requested_before[0],
                requested_after[1] - requested_before[1],
            ]
            undo = _require_ok(client.request("editor_task0_undo", {}), "rotation product undo")
            undo_status = _wait_for_status(
                client,
                lambda status: list(status.get("preview_position") or []) == requested_before,
                timeout=8.0,
                poll_name="rotation product undo",
            )
            redo = _require_ok(client.request("editor_task0_redo", {}), "rotation product redo")
            redo_status = _wait_for_status(
                client,
                lambda status: list(status.get("preview_position") or []) == requested_after,
                timeout=8.0,
                poll_name="rotation product redo",
            )
            report["product_undo"] = {
                "ok": list(undo_status.get("preview_position") or []) == requested_before
                and list(redo_status.get("preview_position") or []) == requested_after,
                "undo": undo,
                "redo": redo,
            }
            write = _attempt_save_and_rebind(
                client,
                fixture_path=fixture_path,
                expected_move=requested_move,
            )
            report["write_chain"] = write

            if write.get("ok") is not True:
                verdict = "blocked"
                report["verdict_reason"] = "write_chain_failed"
            else:
                verdict = "pass" if report["product_undo"]["ok"] else "blocked"
                report["verdict_reason"] = None if verdict == "pass" else "manual_undo_only"

            post_obs = _observe_selected(client)
            post_rect = post_obs.get("rect") or []
            report["write_chain"].update(
                {
                    "requested_before": requested_before,
                    "requested_after": requested_after,
                    "requested_move": requested_move,
                    "pre_observation_rect": pre_rect,
                    "post_observation_rect": post_rect,
                }
            )

    report["manual_rotate_roundtrip"] = _run_manual_rotate_roundtrip(fixture_path)

    rotated = report["transform_plane"].get(TARGET_ID) or {}
    paint = report["isolation"][TARGET_ID]["point_samples"]
    manual = report["manual_rotate_roundtrip"]
    if not manual.get("outside_bytes_equal") or not manual.get("matches_baseline"):
        report["verdict"] = "inconclusive"
        report["verdict_reason"] = "manual_roundtrip_failed"
    elif not rotated.get("quad_available"):
        report["verdict"] = "blocked"
        report["verdict_reason"] = "missing_transform_seam"
    elif not paint["center"]["painted"] or not paint["edge"]["painted"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "paint_mask_incomplete"
    elif report["aabb_corner_probe"]["selected_rotated"] and not report["aabb_corner_probe"]["painted"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "aabb_false_positive"
    else:
        report["verdict"] = verdict

    fixture_path.write_bytes(baseline_bytes)
    report["fixture_restore"] = {
        "matches_baseline": fixture_path.read_bytes() == baseline_bytes,
        "sha256": _sha256_file(fixture_path),
    }

    return report
