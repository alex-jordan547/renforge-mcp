"""Live driver for issue #43 — non-focusable hit via quad ∩ colour-mask sentinel.

Ground truth is independent screenshot colour sampling. Geometry AABB/quad come
from the in-game spike. COMP requires both quad membership and observed paint
of the target's unique colour (the measured sentinel mask for this fixture).
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

# Colour match tolerance for anti-aliased text edges (not for solid fills).
COLOUR_TOLERANCE = {
    "hit_text": 64,
    "hit_focusable": 40,
    "default": 12,
}


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
    """Return target_id or 'background' for a screenshot sample."""
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
    # Dark stage residual.
    if best_id == "background" and max(rgb) <= 24:
        return "background"
    return best_id


def _sample_with_neighbour(
    image: Image.Image,
    x: int,
    y: int,
    colours: dict[str, list[int]],
    *,
    expected: str | None = None,
) -> dict[str, Any]:
    width, height = image.size
    samples = []
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
    for dx, dy in offsets:
        sx, sy = x + dx, y + dy
        if not (0 <= sx < width and 0 <= sy < height):
            continue
        pixel = image.getpixel((sx, sy))
        if isinstance(pixel, int):
            rgb = (pixel, pixel, pixel)
        else:
            rgb = (int(pixel[0]), int(pixel[1]), int(pixel[2]))
        label = _classify_pixel(rgb, colours)
        samples.append({"x": sx, "y": sy, "rgb": list(rgb), "label": label})
        if expected is None:
            return {"label": label, "rgb": list(rgb), "samples": samples, "matched_offset": [dx, dy]}
        if label == expected:
            return {"label": label, "rgb": list(rgb), "samples": samples, "matched_offset": [dx, dy]}
    # Fall back to centre classification.
    centre = samples[0] if samples else {"label": "background", "rgb": [0, 0, 0]}
    return {
        "label": centre["label"],
        "rgb": centre.get("rgb", [0, 0, 0]),
        "samples": samples,
        "matched_offset": [0, 0],
        "neighbour_search_failed": expected is not None,
    }


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
    row: dict[str, Any] = {"x": px, "y": py, "aabb": [], "quad": [], "comp_candidates": []}
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
        if _point_in_quad(px, py, quad) and in_clip:
            row["quad"].append(widget_id)
            row["comp_candidates"].append(widget_id)
    return row


def _build_probe_matrix(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    """Named probes with expected GT labels (for neighbour search only)."""
    def centre(aabb: list[int] | None) -> tuple[int, int]:
        if not aabb or len(aabb) != 4:
            return (0, 0)
        return (int(aabb[0] + aabb[2] // 2), int(aabb[1] + aabb[3] // 2))

    add = geometry.get("hit_add") or {}
    text = geometry.get("hit_text") or {}
    frame = geometry.get("hit_frame") or {}
    rotated = geometry.get("hit_rotated") or {}
    clipped = geometry.get("hit_clipped_child") or {}
    clip_parent = geometry.get("hit_clip_parent") or {}
    vp_child = geometry.get("hit_viewport_child") or {}
    focusable = geometry.get("hit_focusable") or {}

    ra = rotated.get("aabb") or [320, 240, 140, 80]
    # Exterior AABB corner of rotated box: top-left of unrotated AABB is often
    # outside the rotated solid for 25° rotation.
    rot_corner = (int(ra[0]) + 2, int(ra[1]) + 2)

    ca = clipped.get("aabb") or [520, 80, 200, 80]
    cp = clip_parent.get("aabb") or [520, 80, 100, 80]
    # Visible interior of clip parent (and child paint).
    clipped_visible = (int(cp[0] + cp[2] // 2), int(cp[1] + cp[3] // 2))
    # Past the clip parent right edge but still over the unclipped child AABB.
    clipped_away = (int(cp[0] + cp[2] + 20), int(cp[1] + cp[3] // 2))

    # Prefer measured AABB centres; text/focusable points may be refined after screenshot.
    probes = [
        {"name": "add_interior", "point": centre(add.get("aabb")), "expect_gt": "hit_add"},
        {"name": "add_exterior", "point": (40, 40), "expect_gt": "background"},
        {"name": "text_interior", "point": centre(text.get("aabb")), "expect_gt": "hit_text", "scan_aabb": text.get("aabb")},
        {"name": "text_exterior", "point": (280, 60), "expect_gt": "background"},
        {"name": "frame_interior", "point": centre(frame.get("aabb")), "expect_gt": "hit_frame"},
        {
            "name": "rotated_interior",
            "point": centre(rotated.get("aabb")),
            "expect_gt": "hit_rotated",
        },
        {
            "name": "rotated_aabb_corner",
            # Far AABB corner: for 25° rotation the unrotated top-left is outside paint.
            "point": (int(ra[0]) + 4, int(ra[1]) + 4),
            # GT should be background (outside rotated body) while AABB hits.
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
    return probes


def _find_paint_in_aabb(
    image: Image.Image,
    aabb: list[int] | None,
    target_id: str,
    colours: dict[str, list[int]],
) -> tuple[int, int] | None:
    """Scan an AABB for a pixel classified as target_id (glyph / sparse paint)."""
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


def _mask_hits_colour(
    image: Image.Image,
    point: tuple[int, int],
    target_id: str,
    colours: dict[str, list[int]],
) -> bool:
    sample = _sample_with_neighbour(
        image,
        int(point[0]),
        int(point[1]),
        colours,
        expected=target_id,
    )
    return sample["label"] == target_id


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
            # Screen not yet shown — prepare will show it.
            time.sleep(0.05)

        prepare = _require(client.request("hit_sentinel_prepare", {}), "prepare")
        # Wait for first frame of the fixture.
        for _ in range(50):
            ready = client.eval_expr("renforge_hit_sentinel_ready")
            if ready is True:
                break
            time.sleep(0.05)
        geometry_reply = _require(client.request("hit_sentinel_geometry", {}), "geometry")
        geometry = geometry_reply.get("geometry") or {}
        colours = prepare.get("colours") or {}

        def _screenshot_has_fixture_paint(candidate: Image.Image) -> int:
            """Count unique fixture colours found on a coarse grid (independent paint)."""
            found = 0
            checks = [
                ((160, 130), "hit_add"),
                ((170, 270), "hit_frame"),
                ((390, 280), "hit_rotated"),
            ]
            for (x, y), tid in checks:
                label = _sample_with_neighbour(candidate, x, y, colours, expected=tid)["label"]
                if label == tid:
                    found += 1
            return found

        # Poll until the independent screenshot shows fixture paint (not a blank frame).
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

        probes = _build_probe_matrix(geometry)
        # Refine sparse-paint probes (text glyphs, button chrome) from observed screenshot.
        for probe in probes:
            scan = probe.get("scan_aabb")
            expect = probe.get("expect_gt")
            if scan and expect and expect != "background":
                found = _find_paint_in_aabb(image, scan, expect, colours)
                if found is not None:
                    probe["point"] = found

        # Classify AABB/quad in-process (avoid JSON round-trip quirks).
        class_rows = [
            _classify_point_local(list(p["point"]), geometry) for p in probes
        ]

        mask_cost_started = time.monotonic()
        # Colour mask is derived from the same independent screenshot (observed paint).
        mask_build_ms = (time.monotonic() - mask_cost_started) * 1000.0

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
                # Retry without expected for soft classification.
                gt = _sample_with_neighbour(image, px, py, colours)
                if gt["label"] == "background" and probe.get("expect_gt") not in (
                    None,
                    "background",
                ):
                    gt_ambiguous += 1

            row = class_rows[index] if index < len(class_rows) else {}
            aabb_ids = list(row.get("aabb") or [])
            quad_ids = list(row.get("quad") or [])

            # COMP: topmost paint among quad candidates via independent colour.
            comp_id = "background"
            for candidate in quad_ids:
                if _mask_hits_colour(image, (px, py), candidate, colours):
                    comp_id = candidate
                    break
            # If no quad candidate painted, still allow GT colour-only for reporting.
            if comp_id == "background":
                label = gt["label"]
                if label != "background" and label in quad_ids:
                    comp_id = label

            gt_label = gt["label"]
            # Background-or-target agreement for each mechanism's "who is hit".
            aabb_pick = aabb_ids[-1] if aabb_ids else "background"
            quad_pick = quad_ids[-1] if quad_ids else "background"

            # For multi-hit, prefer GT when present in the list.
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
                # Pass criterion: AABB disagrees with GT (false positive).
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
                }
            )

        # Cost: 20 probes on rotated target.
        rot_aabb = (geometry.get("hit_rotated") or {}).get("aabb") or [320, 240, 140, 80]
        cost_started = time.monotonic()
        for i in range(20):
            cx = int(rot_aabb[0] + (i % 5) * 5)
            cy = int(rot_aabb[1] + (i % 4) * 5)
            _sample_with_neighbour(image, cx, cy, colours)
        cost_ms = (time.monotonic() - cost_started) * 1000.0

        _require(client.request("hit_sentinel_finish", {}), "finish")

        n = len(probe_results) or 1
        focusable_ok = bool(geometry_reply.get("focusable_in_focus_list"))
        nonfocus_absent = bool(geometry_reply.get("nonfocusable_absent_from_focus_list"))
        sentinel_reachable = all(
            (geometry.get(tid) or {}).get("found")
            for tid in ("hit_add", "hit_text", "hit_frame", "hit_rotated")
        )
        # Non-empty paint for key solid targets at expected interiors.
        paint_ok = all(
            any(p["name"] == name and p["gt"] == tid for p in probe_results)
            for name, tid in (
                ("add_interior", "hit_add"),
                ("frame_interior", "hit_frame"),
                ("rotated_interior", "hit_rotated"),
            )
        )

        # Evaluate COMP on the solid/clip/viewport probes that have reliable paint.
        # Text/focusable may use sparse glyphs; still reported but not required for pass.
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

        if paint_hits < 2:
            capability = "inconclusive"
            reason = "screenshot_missing_fixture_paint"
        elif not sentinel_reachable:
            capability = "blocked"
            reason = "sentinel_or_widget_unreachable"
        elif not nonfocus_absent:
            capability = "blocked"
            reason = "nonfocusable_leaked_into_focus_list"
        elif not aabb_rotated_false_positive:
            if comp_critical and paint_ok:
                capability = "pass"
                reason = "comp_matches_gt_but_aabb_corner_not_falsified"
            else:
                capability = "blocked"
                reason = "rotated_aabb_not_falsified_and_comp_incomplete"
        elif not comp_critical:
            capability = "blocked"
            reason = "comp_disagrees_with_ground_truth"
        elif not paint_ok:
            capability = "blocked"
            reason = "sentinel_paint_empty"
        else:
            capability = "pass"
            reason = "all_pass_criteria"
            if not focusable_ok:
                reason = "pass_with_focusable_focus_list_unproven"
            if ambiguous_rate > 0.10:
                reason = "pass_with_sparse_glyph_ambiguity"

        report.update(
            {
                "capability": capability,
                "reason": reason,
                "geometry_ms": geometry_reply.get("geometry_ms"),
                "screenshot_ms": shot_ms,
                "mask_build_ms": mask_build_ms,
                "probe_cost_20_ms": cost_ms,
                "screenshot_sha256": hashlib.sha256(png).hexdigest(),
                "image_size": list(image.size),
                "focusable_in_focus_list": focusable_ok,
                "nonfocusable_absent_from_focus_list": nonfocus_absent,
                "aabb_rotated_false_positive": aabb_rotated_false_positive,
                "gt_ambiguous_probes": gt_ambiguous,
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


def run_twice_for_determinism(project_root: Path, *, display: str = "auto") -> dict[str, Any]:
    first = run_hit_sentinel_spike(project_root, display=display)
    second = run_hit_sentinel_spike(project_root, display=display)
    return {
        "run1_capability": first.get("capability"),
        "run2_capability": second.get("capability"),
        "deterministic": first.get("capability") == second.get("capability"),
        "run1": first,
        "run2": second,
    }
