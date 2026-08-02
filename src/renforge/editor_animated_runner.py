"""Live evidence runner for issue #51 animated-element editing through _widget_properties seam."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor.paths import atomic_write_file
from renforge.editor_live_common import wait_bounds
from renforge.editor_task0_runner import _require_ok

FIXTURE_SCREEN = "renforge_editor_animated_fixture"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_animated_fixture.rpy"
)


def inject_editor_animated_resources(project_root: Path) -> Path:
    target = project_root / "game" / "zz_renforge_editor_animated_fixture.rpy"
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
    raise AssertionError(f"fixture did not start: {last!r}")


def run_editor_animated_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    baseline = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline).hexdigest()
    source = baseline.decode("utf-8")

    report: dict[str, Any] = {
        "baseline_sha256": baseline_sha,
        "variants": {},
    }

    _show_fixture(client)

    # Variant 1: ATL position animation target
    pos_target_initial = wait_bounds(client, "anim_pos_target", fixture_screen=FIXTURE_SCREEN)
    
    # Try previewing position override via _widget_properties seam
    preview_pos = _require_ok(
        client.request(
            "editor_task0_preview",
            {
                "screen": FIXTURE_SCREEN,
                "widget_id": "anim_pos_target",
                "xpos": 250,
                "ypos": 100,
            },
        ),
        "anim_pos_target preview",
    )

    # Give a short frame interval to see if ATL animation overwrites xpos override
    time.sleep(0.2)
    pos_target_during_preview = wait_bounds(client, "anim_pos_target", fixture_screen=FIXTURE_SCREEN)

    # Check if ATL position animation overwrote the preview xpos or if it held
    atl_position_conflict = pos_target_during_preview.get("x") != 250

    report["variants"]["anim_pos_target"] = {
        "initial_bounds": pos_target_initial,
        "requested_preview": {"xpos": 250, "ypos": 100},
        "preview_reply": preview_pos,
        "observed_preview_bounds": pos_target_during_preview,
        "atl_position_conflict": atl_position_conflict,
    }

    # Variant 2: Non-positional ATL pulse animation target
    style_target_initial = wait_bounds(client, "anim_style_target", fixture_screen=FIXTURE_SCREEN)
    
    st_before = client.eval_expr('getattr(renpy.get_widget("renforge_editor_animated_fixture", "anim_style_target"), "st", None)')
    time.sleep(0.3)
    st_mid = client.eval_expr('getattr(renpy.get_widget("renforge_editor_animated_fixture", "anim_style_target"), "st", None)')

    # Send preview request to observe ATL time/state resets
    preview_style1 = _require_ok(
        client.request(
            "editor_task0_preview",
            {
                "screen": FIXTURE_SCREEN,
                "widget_id": "anim_style_target",
                "xpos": 420,
                "ypos": 100,
            },
        ),
        "anim_style_target preview 1",
    )
    st_after = client.eval_expr('getattr(renpy.get_widget("renforge_editor_animated_fixture", "anim_style_target"), "st", None)')

    time.sleep(0.1)
    preview_style2 = _require_ok(
        client.request(
            "editor_task0_preview",
            {
                "screen": FIXTURE_SCREEN,
                "widget_id": "anim_style_target",
                "xpos": 440,
                "ypos": 100,
            },
        ),
        "anim_style_target preview 2",
    )

    style_target_during_preview = wait_bounds(client, "anim_style_target", fixture_screen=FIXTURE_SCREEN)

    # Derive atl_time_reset dynamically from sampled show time (st) reset or displayable re-instantiation
    atl_time_reset = (
        (isinstance(st_mid, (int, float)) and isinstance(st_after, (int, float)) and st_after < st_mid)
        or st_before is None
        or st_after is None
    )

    report["variants"]["anim_style_target"] = {
        "initial_bounds": style_target_initial,
        "requested_previews": [{"xpos": 420, "ypos": 100}, {"xpos": 440, "ypos": 100}],
        "observed_preview_bounds": style_target_during_preview,
        "sampled_st": {"before": st_before, "mid": st_mid, "after": st_after},
        "atl_time_reset": atl_time_reset,
    }

    # Variant 3: Stationary Transform wrapper target
    static_target_initial = wait_bounds(client, "anim_static_transform", fixture_screen=FIXTURE_SCREEN)

    # Test patch and reload on static transform target
    assert "xpos 100 ypos 300" in source, "Fixture format changed; expected static transform coordinates not found"
    patched_source = source.replace("xpos 100 ypos 300", "xpos 120 ypos 300")
    if patched_source == source:
        raise AssertionError("source patch did not modify target string")

    try:
        atomic_write_file(fixture_path, patched_source)
        save_reply = _require_ok(
            client.request("editor_task0_save", {"screen": FIXTURE_SCREEN}),
            "anim_static_transform save",
        )
        time.sleep(0.3)

        static_target_post_reload = wait_bounds(client, "anim_static_transform", fixture_screen=FIXTURE_SCREEN)
        select_reply = _require_ok(
            client.request("editor_task0_select", {"x": 130, "y": 320}),
            "anim_static_transform select",
        )

        report["variants"]["anim_static_transform"] = {
            "initial_bounds": static_target_initial,
            "post_reload_bounds": static_target_post_reload,
            "save_reply": save_reply,
            "select_reply": select_reply,
        }
    finally:
        atomic_write_file(fixture_path, source)

    # Determine verdict based on evidence
    # ATL position animation conflict or ATL time reset on displayable recreation means BLOCKED
    if atl_position_conflict or report["variants"]["anim_style_target"]["atl_time_reset"]:
        report["verdict"] = "blocked"
        report["reason_code"] = (
            "atl_position_override_conflict"
            if atl_position_conflict
            else "atl_time_reset"
        )
    else:
        report["verdict"] = "pass"

    return report
