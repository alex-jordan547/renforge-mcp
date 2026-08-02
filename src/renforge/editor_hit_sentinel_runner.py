"""Live driver for issue #43 — non-focusable hit via quad ∩ isolated sentinel mask.

Ground truth (GT): independent full-scene screenshot colour classification.
COMP: runtime transformed quad ∩ candidate-isolated paint mask (siblings alpha=0).
Isolation masks are built per target and are independent of multi-target GT colours.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.bridge.launcher import launch_with_bridge
from renforge.project import RenpyProject
from renforge.sdk import get_or_install_sdk

EXPECTED_SDK = "8.5.3"
FIXTURE_SCREEN = "renforge_hit_sentinel_fixture"
SPIKE_RESOURCE = Path(__file__).resolve().parent / "bridge" / "hit_sentinel_spike.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_hit_sentinel_fixture.rpy"
)

COLOUR_TOLERANCE = {
    "hit_text": 64,
    "hit_focusable": 40,
    "default": 12,
}

# Targets that get isolation masks (must match spike ISOLATION_IDS).
ISOLATION_TARGETS = (
    "hit_add",
    "hit_text",
    "hit_frame",
    "hit_rotated",
    "hit_clipped_child",
    "hit_viewport_child",
    "hit_focusable",
)

BG_LUMA_MAX = 28


def inject_hit_sentinel_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    spike_target = game_dir / "zz_renforge_hit_sentinel_spike.rpy"
    fixture_target = game_dir / "zz_renforge_hit_sentinel_fixture.rpy"
    shutil.copyfile(SPIKE_RESOURCE, spike_target)
    shutil.copyfile(FIXTURE_RESOURCE, fixture_target)
    return {"spike": str(spike_target), "fixture": str(fixture_target)}


def _require(reply: dict[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise RuntimeError(f"{name} failed: {reply!r}")
    return reply


def _colour_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _classify_pixel(
    rgb: tuple[int, int, int],
    colours: dict[str, list[int]],
) -> str:
    best_id = "background"
    best_dist = 10**9
    for target_id, colour in colours.items():
        if len(colour) != 3:
            continue
        tol = COLOUR_TOLERANCE.get(target_id, COLOUR_TOLERANCE["default"])
        dist = _colour_distance(rgb, (int(colour[0]), int(colour[1]), int(colour[2])))
        if dist <= tol and dist < best_dist:
            best_dist = dist
            best_id = target_id
    if best_id == "background" and max(rgb) <= BG_LUMA_MAX:
        return "background"
    return best_id


def _sample_rgb(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    width, height = image.size
    if not (0 <= x < width and 0 <= y < height):
        return (0, 0, 0)
    pixel = image.getpixel((x, y))
    if isinstance(pixel, int):
        return (pixel, pixel, pixel)
    return (int(pixel[0]), int(pixel[1]), int(pixel[2]))


def _sample_with_neighbour(
    image: Image.Image,
    x: int,
    y: int,
    colours: dict[str, list[int]],
    *,
    expected: str | None = None,
) -> dict[str, Any]:
    offsets = [
        (0, 0),
        (-1, 0),
        (1, 0),
        (0, -1),
        (0, 1),
        (-1, -1),
        (1, -1),
        (-1, 1),
        (1, 1),
    ]
    samples = []
    for dx, dy in offsets:
        sx, sy = x + dx, y + dy
        rgb = _sample_rgb(image, sx, sy)
        label = _classify_pixel(rgb, colours)
        samples.append({"x": sx, "y": sy, "rgb": list(rgb), "label": label})
        if expected is None:
            return {
                "label": label,
                "rgb": list(rgb),
                "samples": samples,
                "matched_offset": [dx, dy],
            }
        if label == expected:
            return {
                "label": label,
                "rgb": list(rgb),
                "samples": samples,
                "matched_offset": [dx, dy],
            }
    centre = samples[0] if samples else {"label": "background", "rgb": [0, 0, 0]}
    return {
        "label": centre["label"],
        "rgb": centre.get("rgb", [0, 0, 0]),
        "samples": samples,
        "matched_offset": [0, 0],
        "neighbour_search_failed": expected is not None,
    }


def _is_paint_pixel(rgb: tuple[int, int, int]) -> bool:
    """Isolation mask: any non-dark-stage pixel counts as painted sentinel."""
    return max(rgb) > BG_LUMA_MAX


def _build_isolation_mask(
    image: Image.Image,
    *,
    roi: list[int] | None = None,
) -> set[tuple[int, int]]:
    """Sparse set of painted pixels from a candidate-isolated screenshot.

    Optionally limit scan to an expanded ROI around the measured AABB for speed.
    """
    width, height = image.size
    if roi and len(roi) == 4:
        x0 = max(0, int(roi[0]) - 80)
        y0 = max(0, int(roi[1]) - 80)
        x1 = min(width, int(roi[0] + roi[2]) + 80)
        y1 = min(height, int(roi[1] + roi[3]) + 80)
    else:
        x0, y0, x1, y1 = 0, 0, width, height
    painted: set[tuple[int, int]] = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if _is_paint_pixel(_sample_rgb(image, x, y)):
                painted.add((x, y))
    return painted


def _mask_contains(mask: set[tuple[int, int]], x: int, y: int, *, radius: int = 1) -> bool:
    if (x, y) in mask:
        return True
    if radius <= 0:
        return False
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if (x + dx, y + dy) in mask:
                return True
    return False


def _point_in_aabb(px: float, py: float, aabb: list[int] | None) -> bool:
    if not aabb or len(aabb) != 4:
        return False
    x, y, w, h = aabb
    return x <= px < x + w and y <= py < y + h


def _point_in_quad(px: float, py: float, quad: list[list[float]] | None) -> bool:
    if not quad or len(quad) != 4:
        return False
    inside = False
    j = 3
    for i in range(4):
        xi, yi = float(quad[i][0]), float(quad[i][1])
        xj, yj = float(quad[j][0]), float(quad[j][1])
        intersects = ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _classify_point_local(point: list[int], geometry: dict[str, Any]) -> dict[str, Any]:
    px, py = int(point[0]), int(point[1])
    row: dict[str, Any] = {"x": px, "y": py, "aabb": [], "quad": []}
    for widget_id, geo in geometry.items():
        if not isinstance(geo, dict) or not str(widget_id).startswith("hit_"):
            continue
        if widget_id in {"hit_clip_parent", "hit_viewport", "hit_root"}:
            continue
        aabb = geo.get("aabb")
        quad = geo.get("quad")
        clip = geo.get("clip_rect")
        in_clip = True if clip is None else _point_in_aabb(px, py, clip)
        if _point_in_aabb(px, py, aabb) and in_clip:
            row["aabb"].append(widget_id)
        if quad and _point_in_quad(px, py, quad) and in_clip:
            row["quad"].append(widget_id)
    return row


def _build_probe_matrix(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    def centre(aabb: list[int] | None) -> tuple[int, int]:
        if not aabb or len(aabb) != 4:
            return (0, 0)
        return (int(aabb[0] + aabb[2] // 2), int(aabb[1] + aabb[3] // 2))

    add = geometry.get("hit_add") or {}
    text = geometry.get("hit_text") or {}
    frame = geometry.get("hit_frame") or {}
    rotated = geometry.get("hit_rotated") or {}
    clip_parent = geometry.get("hit_clip_parent") or {}
    vp_child = geometry.get("hit_viewport_child") or {}
    focusable = geometry.get("hit_focusable") or {}

    ra = rotated.get("aabb") or [320, 240, 140, 80]
    cp = clip_parent.get("aabb") or [520, 80, 100, 80]
    clipped_visible = (int(cp[0] + cp[2] // 2), int(cp[1] + cp[3] // 2))
    clipped_away = (int(cp[0] + cp[2] + 20), int(cp[1] + cp[3] // 2))

    return [
        {"name": "add_interior", "point": centre(add.get("aabb")), "expect_gt": "hit_add"},
        {"name": "add_exterior", "point": (40, 40), "expect_gt": "background"},
        {
            "name": "text_interior",
            "point": centre(text.get("aabb")),
            "expect_gt": "hit_text",
            "scan_aabb": text.get("aabb"),
        },
        {"name": "text_exterior", "point": (280, 60), "expect_gt": "background"},
        {"name": "frame_interior", "point": centre(frame.get("aabb")), "expect_gt": "hit_frame"},
        {
            "name": "rotated_interior",
            "point": centre(rotated.get("aabb")),
            "expect_gt": "hit_rotated",
        },
        {
            "name": "rotated_aabb_corner",
            "point": (int(ra[0]) + 4, int(ra[1]) + 4),
            "expect_gt": "background",
        },
        {
            "name": "clipped_visible",
            "point": clipped_visible,
            "expect_gt": "hit_clipped_child",
        },
        {
            "name": "clipped_away",
            "point": clipped_away,
            "expect_gt": "background",
        },
        {
            "name": "viewport_child",
            "point": centre(vp_child.get("aabb")),
            "expect_gt": "hit_viewport_child",
        },
        {
            "name": "viewport_off_scroll",
            "point": (int((vp_child.get("aabb") or [540, 260, 120, 60])[0] + 60), 210),
            "expect_gt": "background",
        },
        {
            "name": "focusable_centre",
            "point": centre(focusable.get("aabb")),
            "expect_gt": "hit_focusable",
            "scan_aabb": focusable.get("aabb"),
        },
    ]


def _find_paint_in_aabb(
    image: Image.Image,
    aabb: list[int] | None,
    target_id: str,
    colours: dict[str, list[int]],
) -> tuple[int, int] | None:
    if not aabb or len(aabb) != 4:
        return None
    x0, y0, w, h = [int(v) for v in aabb]
    step = 2
    for y in range(y0, y0 + max(1, h), step):
        for x in range(x0, x0 + max(1, w), step):
            sample = _sample_with_neighbour(image, x, y, colours, expected=target_id)
            if sample["label"] == target_id:
                return (x, y)
    return None


def _capture_isolation_masks(
    client: Any,
    *,
    geometry: dict[str, Any],
) -> tuple[dict[str, set[tuple[int, int]]], dict[str, Any]]:
    """Build per-candidate paint masks via sibling alpha=0 isolation."""
    masks: dict[str, set[tuple[int, int]]] = {}
    meta: dict[str, Any] = {"per_target_ms": {}, "pixel_counts": {}, "errors": []}
    total_started = time.monotonic()
    for target_id in ISOLATION_TARGETS:
        t0 = time.monotonic()
        try:
            iso = _require(
                client.request("hit_sentinel_isolate", {"widget_id": target_id}),
                f"isolate:{target_id}",
            )
        except Exception as exc:
            meta["errors"].append(f"{target_id}:isolate:{exc}")
            masks[target_id] = set()
            continue
        # Wait a frame for alpha to take effect.
        time.sleep(0.05)
        png = client.screenshot()
        image = Image.open(io.BytesIO(png)).convert("RGB")
        roi = (geometry.get(target_id) or {}).get("aabb")
        # Rotated solid needs a larger ROI for the swept AABB.
        if target_id == "hit_rotated" and roi and len(roi) == 4:
            pad = 60
            roi = [roi[0] - pad, roi[1] - pad, roi[2] + 2 * pad, roi[3] + 2 * pad]
        mask = _build_isolation_mask(image, roi=roi if isinstance(roi, list) else None)
        masks[target_id] = mask
        meta["pixel_counts"][target_id] = len(mask)
        meta["per_target_ms"][target_id] = (time.monotonic() - t0) * 1000.0
        meta.setdefault("isolate_replies", {})[target_id] = {
            "applied": iso.get("applied"),
            "failed": iso.get("failed"),
        }
    try:
        _require(client.request("hit_sentinel_restore", {}), "restore")
    except Exception as exc:
        meta["errors"].append(f"restore:{exc}")
    time.sleep(0.05)
    meta["total_ms"] = (time.monotonic() - total_started) * 1000.0
    meta["reachable"] = {
        tid: meta["pixel_counts"].get(tid, 0) > 0
        for tid in ("hit_add", "hit_text", "hit_frame", "hit_rotated")
    }
    return masks, meta


def run_hit_sentinel_spike(
    project_root: Path,
    *,
    output: Path | None = None,
    display: str = "auto",
) -> dict[str, Any]:
    inject_hit_sentinel_resources(project_root)
    project = RenpyProject(project_root.resolve())
    sdk = get_or_install_sdk(EXPECTED_SDK, project_root=project.root)
    report: dict[str, Any] = {
        "spike": "issue-43-hit-sentinel",
        "sdk": EXPECTED_SDK,
        "criteria": "docs/superpowers/spikes/2026-08-02-non-focusable-hit-sentinel-criteria.md",
    }

    with launch_with_bridge(
        sdk,
        project,
        display=display,
        audio="dummy",
        savedir="temporary",
        persistent="empty",
    ) as session:
        client = session.client
        for _ in range(40):
            if client.eval_expr("renpy.has_screen(%r)" % FIXTURE_SCREEN) is True:
                break
            time.sleep(0.05)

        prepare = _require(client.request("hit_sentinel_prepare", {}), "prepare")
        for _ in range(50):
            if client.eval_expr("renforge_hit_sentinel_ready") is True:
                break
            time.sleep(0.05)

        geometry_reply = _require(client.request("hit_sentinel_geometry", {}), "geometry")
        geometry = geometry_reply.get("geometry") or {}
        colours = prepare.get("colours") or {}

        def _screenshot_has_fixture_paint(candidate: Image.Image) -> int:
            found = 0
            for (x, y), tid in (
                ((160, 130), "hit_add"),
                ((170, 270), "hit_frame"),
                ((390, 280), "hit_rotated"),
            ):
                if _sample_with_neighbour(candidate, x, y, colours, expected=tid)["label"] == tid:
                    found += 1
            return found

        shot_started = time.monotonic()
        image: Image.Image | None = None
        png = b""
        paint_hits = 0
        for _ in range(60):
            png = client.screenshot()
            candidate = Image.open(io.BytesIO(png)).convert("RGB")
            paint_hits = _screenshot_has_fixture_paint(candidate)
            if paint_hits >= 2:
                image = candidate
                break
            time.sleep(0.05)
        if image is None:
            image = Image.open(io.BytesIO(png)).convert("RGB")
        shot_ms = (time.monotonic() - shot_started) * 1000.0
        report["fixture_paint_hits"] = paint_hits

        # Candidate-isolated sentinel masks (real work, timed).
        isolation_masks, isolation_meta = _capture_isolation_masks(client, geometry=geometry)
        report["isolation"] = {
            "total_ms": isolation_meta.get("total_ms"),
            "per_target_ms": isolation_meta.get("per_target_ms"),
            "pixel_counts": isolation_meta.get("pixel_counts"),
            "reachable": isolation_meta.get("reachable"),
            "errors": isolation_meta.get("errors"),
        }

        # Re-take full-scene GT after restore.
        time.sleep(0.05)
        png = client.screenshot()
        image = Image.open(io.BytesIO(png)).convert("RGB")
        paint_hits = _screenshot_has_fixture_paint(image)
        report["fixture_paint_hits_after_restore"] = paint_hits

        probes = _build_probe_matrix(geometry)
        for probe in probes:
            scan = probe.get("scan_aabb")
            expect = probe.get("expect_gt")
            if scan and expect and expect != "background":
                found = _find_paint_in_aabb(image, scan, expect, colours)
                if found is not None:
                    probe["point"] = found

        class_rows = [_classify_point_local(list(p["point"]), geometry) for p in probes]

        probe_results = []
        aabb_matches = 0
        quad_matches = 0
        comp_matches = 0
        gt_ambiguous = 0
        aabb_rotated_false_positive = False

        for index, probe in enumerate(probes):
            px, py = int(probe["point"][0]), int(probe["point"][1])
            gt = _sample_with_neighbour(
                image,
                px,
                py,
                colours,
                expected=probe.get("expect_gt"),
            )
            if gt.get("neighbour_search_failed") and probe.get("expect_gt") not in (
                None,
                "background",
            ):
                gt = _sample_with_neighbour(image, px, py, colours)
                if gt["label"] == "background" and probe.get("expect_gt") not in (
                    None,
                    "background",
                ):
                    gt_ambiguous += 1

            row = class_rows[index] if index < len(class_rows) else {}
            aabb_ids = list(row.get("aabb") or [])
            quad_ids = list(row.get("quad") or [])

            # COMP: among targets whose runtime quad contains the point, require
            # the candidate-isolated paint mask (not full-scene GT colour).
            comp_id = "background"
            for candidate in quad_ids:
                mask = isolation_masks.get(candidate) or set()
                if _mask_contains(mask, px, py, radius=1):
                    comp_id = candidate
                    break

            gt_label = gt["label"]
            aabb_pick = aabb_ids[-1] if aabb_ids else "background"
            quad_pick = quad_ids[-1] if quad_ids else "background"
            if gt_label in aabb_ids:
                aabb_pick = gt_label
            elif not aabb_ids:
                aabb_pick = "background"
            if gt_label in quad_ids:
                quad_pick = gt_label
            elif not quad_ids:
                quad_pick = "background"

            aabb_ok = aabb_pick == gt_label
            quad_ok = quad_pick == gt_label
            comp_ok = comp_id == gt_label
            if aabb_ok:
                aabb_matches += 1
            if quad_ok:
                quad_matches += 1
            if comp_ok:
                comp_matches += 1

            if probe["name"] == "rotated_aabb_corner":
                if gt_label == "background" and "hit_rotated" in aabb_ids:
                    aabb_rotated_false_positive = True

            probe_results.append(
                {
                    "name": probe["name"],
                    "point": [px, py],
                    "expect_gt": probe.get("expect_gt"),
                    "gt": gt_label,
                    "gt_rgb": gt.get("rgb"),
                    "aabb_ids": aabb_ids,
                    "quad_ids": quad_ids,
                    "aabb_pick": aabb_pick,
                    "quad_pick": quad_pick,
                    "comp_pick": comp_id,
                    "aabb_matches_gt": aabb_ok,
                    "quad_matches_gt": quad_ok,
                    "comp_matches_gt": comp_ok,
                    "isolation_mask_hit": {
                        tid: _mask_contains(isolation_masks.get(tid) or set(), px, py)
                        for tid in ISOLATION_TARGETS
                    },
                }
            )

        rot_aabb = (geometry.get("hit_rotated") or {}).get("aabb") or [320, 240, 140, 80]
        cost_started = time.monotonic()
        rot_mask = isolation_masks.get("hit_rotated") or set()
        for i in range(20):
            cx = int(rot_aabb[0] + (i % 5) * 5)
            cy = int(rot_aabb[1] + (i % 4) * 5)
            _mask_contains(rot_mask, cx, cy)
        cost_ms = (time.monotonic() - cost_started) * 1000.0

        _require(client.request("hit_sentinel_finish", {}), "finish")

        n = len(probe_results) or 1
        focusable_ok = bool(geometry_reply.get("focusable_in_focus_list"))
        nonfocus_absent = bool(geometry_reply.get("nonfocusable_absent_from_focus_list"))
        isolation_reachable = all(
            (isolation_meta.get("reachable") or {}).get(tid)
            for tid in ("hit_add", "hit_text", "hit_frame", "hit_rotated")
        )
        rotated_quad_ok = bool(geometry_reply.get("rotated_quad_available"))
        paint_ok = all(
            any(p["name"] == name and p["gt"] == tid for p in probe_results)
            for name, tid in (
                ("add_interior", "hit_add"),
                ("frame_interior", "hit_frame"),
                ("rotated_interior", "hit_rotated"),
            )
        )
        critical = {
            "add_interior",
            "add_exterior",
            "frame_interior",
            "rotated_interior",
            "rotated_aabb_corner",
            "clipped_visible",
            "clipped_away",
            "viewport_child",
            "viewport_off_scroll",
        }
        critical_rows = [p for p in probe_results if p["name"] in critical]
        comp_critical = all(p["comp_matches_gt"] for p in critical_rows) if critical_rows else False
        ambiguous_rate = gt_ambiguous / float(n)

        # Locked criteria evaluation (strict — no soft pass reasons).
        if paint_hits < 2:
            capability = "inconclusive"
            reason = "screenshot_missing_fixture_paint"
        elif ambiguous_rate > 0.10:
            capability = "inconclusive"
            reason = "ground_truth_ambiguous"
        elif not isolation_reachable:
            capability = "blocked"
            reason = "isolation_sentinel_unreachable"
        elif not rotated_quad_ok:
            capability = "blocked"
            reason = "transform_quad_seam_unavailable"
        elif not nonfocus_absent:
            capability = "blocked"
            reason = "nonfocusable_leaked_into_focus_list"
        elif not focusable_ok:
            capability = "blocked"
            reason = "focusable_not_in_focus_list"
        elif not aabb_rotated_false_positive:
            capability = "blocked"
            reason = "rotated_aabb_not_falsified"
        elif not paint_ok:
            capability = "blocked"
            reason = "sentinel_paint_empty"
        elif not comp_critical:
            capability = "blocked"
            reason = "comp_disagrees_with_ground_truth"
        else:
            capability = "pass"
            reason = "all_pass_criteria"

        report.update(
            {
                "capability": capability,
                "reason": reason,
                "geometry_ms": geometry_reply.get("geometry_ms"),
                "screenshot_ms": shot_ms,
                "mask_build_ms": isolation_meta.get("total_ms"),
                "probe_cost_20_ms": cost_ms,
                "screenshot_sha256": hashlib.sha256(png).hexdigest(),
                "image_size": list(image.size),
                "focusable_in_focus_list": focusable_ok,
                "nonfocusable_absent_from_focus_list": nonfocus_absent,
                "aabb_rotated_false_positive": aabb_rotated_false_positive,
                "rotated_quad_available": rotated_quad_ok,
                "rotated_quad_seam": geometry_reply.get("rotated_quad_seam"),
                "rotated_quad_error": geometry_reply.get("rotated_quad_error"),
                "isolation_reachable": isolation_reachable,
                "gt_ambiguous_probes": gt_ambiguous,
                "gt_ambiguous_rate": ambiguous_rate,
                "agreement": {
                    "aabb": aabb_matches / float(n),
                    "quad": quad_matches / float(n),
                    "comp": comp_matches / float(n),
                    "n": n,
                },
                "probes": probe_results,
                "geometry": geometry,
            }
        )

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def run_twice_for_determinism(
    project_root: Path,
    *,
    display: str = "auto",
    output: Path | None = None,
) -> dict[str, Any]:
    first = run_hit_sentinel_spike(project_root, display=display)
    second = run_hit_sentinel_spike(project_root, display=display)
    result = {
        "run1_capability": first.get("capability"),
        "run2_capability": second.get("capability"),
        "deterministic": first.get("capability") == second.get("capability"),
        "run1": first,
        "run2": second,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
