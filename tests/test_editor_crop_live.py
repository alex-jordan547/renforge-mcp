from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_crop_runner import (
    FIXTURE_SCREEN,
    inject_editor_crop_resources,
    measure_composite_divergence,
    measure_visible_geometry,
    run_editor_crop_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_CROP_LIVE"),
    reason="set RENFORGE_CROP_LIVE=1 to run the Transform(crop=) ancestry proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_crop_resources(destination)
    return destination


def _open_editor(session) -> None:
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
        time.sleep(0.25)
    else:
        pytest.fail("editor launcher never became active")

    assert session.client.click_element(
        text="RF", exact=True, screen="_renforge_editor_launcher"
    ).get("ok") is True
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_overlay").get("active") is True:
            break
        time.sleep(0.05)
    else:
        pytest.fail("editor overlay never became active")

    for _ in range(20):
        if session.client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")') is True:
            break
        time.sleep(0.1)
    else:
        pytest.fail("crop fixture screen never became available")


def test_crop_seven_step_live_proof(demo_copy: Path) -> None:
    """Write chain + visible geometry for a pure Transform(crop=) child."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_crop_fixture.rpy"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = run_editor_crop_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    # Classification: Crop sugar is Transform + transform_crop.
    assert report["resolve"]["lock_reason"] in (None, "")
    assert report["resolve"]["move"] is True
    assert report["resolve"]["measurement_method"] == "focus_list"
    assert report["resolve"]["transform_crop_ancestor_count"] == 1
    assert report["resolve"]["transform_crop_composite_count"] == 0
    assert report["resolve"]["crop_ancestor_types"] == ["Transform"]

    # Visible geometry: engine clips focus under pure crop (not unclipped layout).
    vis = report["visible_geometry"]
    assert vis["target"]["focus_fully_inside_crop"] is True
    assert vis["partial"]["focus_fully_inside_crop"] is True
    assert vis["partial"]["focus_shorter_than_natural"] is True
    assert vis["partial"]["height_delta_from_natural"] > 0
    assert vis["fullclip"]["listed_in_list_ui"] is False
    assert report["visible_geometry_after"]["target"]["focus_fully_inside_crop"] is True

    preview = report["preview"]
    assert preview["bounds_before"] != preview["bounds_after"]
    assert all(
        abs(preview["observed_delta"][axis] - preview["requested_delta"][axis]) <= 1
        for axis in (0, 1)
    )

    patch = report["patch"]
    assert patch["outside_coordinate_spans_identical"] is True
    assert patch["matches_independent_expected"] is True
    assert patch["after_sha256"] != patch["before_sha256"]

    assert report["reload"]["status_text"] == "Reload committed"
    assert report["reload"]["generation_delta"] == 1
    assert all(abs(int(value)) <= 1 for value in report["pixel_agreement"]["delta"])
    assert report["rebinding"]["ok"] is True

    locks = report["locks"]
    assert locks["computed"] == "YPOS_LITERAL_REQUIRED"
    assert locks["container"] == "CONTAINER_POSITION_UNSUPPORTED"
    assert locks["partial"] == "TRANSFORM_CROP_PARTIAL_UNSUPPORTED"
    assert locks["crop_with_rotate"] == "TRANSFORM_CROP_PARTIAL_UNSUPPORTED"
    assert locks["crop_with_zoom"] in (None, "")
    assert report["outside"]["move"] is True

    undo = report["byte_identical_undo"]
    assert undo["matches_baseline"] is True
    assert undo["patched_differed"] is True


def test_crop_visible_geometry_matrix(demo_copy: Path) -> None:
    """Focus rect alone is not sufficient under crop — measure partial vs full clip."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        session.client.request("editor_task0_start", {"screen": FIXTURE_SCREEN})
        report = measure_visible_geometry(session.client)

    # Fully visible target sits inside the crop window.
    assert report["target"]["focus_fully_inside_crop"] is True
    assert report["target"]["focus_center_in_crop"] is True

    # Partial: focus is already clipped to the crop (shorter than natural sibling).
    # This is the decisive measurement — focus is not an unclipped layout box.
    assert report["partial"]["focus_fully_inside_crop"] is True
    assert report["partial"]["focus_shorter_than_natural"] is True
    assert report["partial"]["height_delta_from_natural"] > 0
    assert report["partial"]["focus_height"] < report["natural_height_outside"]

    # Fully clipped: not listed for selection.
    assert report["fullclip"]["listed_in_list_ui"] is False
    assert "crop_fullclip" not in report["listed_ids"]


def test_composite_transform_unblocked_via_inverse_matrix_conversion(demo_copy: Path) -> None:
    """Issue #46: crop+zoom is now unblocked via inverse matrix conversion.

    The editor converts screen deltas using the inverse transform matrix so that
    drag movements map 1:1 on screen regardless of zoom scaling.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = measure_composite_divergence(session.client)

    # crop_with_zoom is unblocked; crop_with_rotate remains locked due to partial crop boundary.
    assert report["locks"]["crop_with_zoom"] in (None, "")
    assert report["locks"]["crop_with_rotate"] == "TRANSFORM_CROP_PARTIAL_UNSUPPORTED"
