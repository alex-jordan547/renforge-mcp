from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_imagebutton_runner import (
    FIXTURE_SCREEN,
    inject_editor_imagebutton_resources,
    run_editor_imagebutton_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_IMAGEBUTTON_LIVE"),
    reason="set RENFORGE_IMAGEBUTTON_LIVE=1 to run imagebutton seven-step live proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_imagebutton_resources(destination)
    return destination


def test_imagebutton_seven_step_live_proof(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_imagebutton_fixture.rpy"

    with launch_with_bridge(
        sdk,
        project,
        startup_timeout=120,
        editor=True,
    ) as session:
        for _ in range(40):
            launcher = session.client.inspect_screen("_renforge_editor_launcher")
            if launcher.get("active") is True:
                break
            time.sleep(0.25)
        else:
            pytest.fail("editor launcher screen never became active")

        launcher_click = session.client.click_element(
            text="RF",
            exact=True,
            screen="_renforge_editor_launcher",
        )
        assert launcher_click.get("ok") is True, launcher_click
        for _ in range(40):
            overlay = session.client.inspect_screen("_renforge_editor_overlay")
            if overlay.get("active") is True:
                break
            time.sleep(0.05)
        else:
            pytest.fail("RF launcher did not activate the editor overlay")

        # Ensure the fixture screen is available before the runner starts it.
        for _ in range(20):
            shown = session.client.eval_expr(
                f'renpy.has_screen("{FIXTURE_SCREEN}")'
            )
            if shown is True:
                break
            time.sleep(0.1)

        report = run_editor_imagebutton_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    assert report["resolve"]["statement_kind"] == "imagebutton"
    assert report["resolve"]["lock_reason"] in (None, "")
    assert report["resolve"]["move"] is True
    assert report["resolve"]["measurement_method"] == "focus_list"

    preview = report["preview"]
    assert preview["bounds_before"] != preview["bounds_after"]
    observed_delta = [
        preview["bounds_after"][axis] - preview["bounds_before"][axis]
        for axis in (0, 1)
    ]
    requested_delta = [
        preview["requested_after"][axis] - preview["requested_before"][axis]
        for axis in (0, 1)
    ]
    assert all(
        abs(observed_delta[axis] - requested_delta[axis]) <= 1
        for axis in (0, 1)
    )
    assert preview["measurement_method"] == "focus_list"

    patch = report["patch"]
    assert patch["outside_coordinate_spans_identical"] is True
    assert patch["after_sha256"] != patch["before_sha256"]
    assert patch["source_position_after"] == {
        "x": preview["requested_after"][0],
        "y": preview["requested_after"][1],
    }
    assert patch["parsed_after"]["xpos"] == patch["source_position_after"]["x"]
    assert patch["parsed_after"]["ypos"] == patch["source_position_after"]["y"]

    assert report["reload"]["ok"] is True
    assert report["reload"]["status_text"] == "Reload committed"
    assert report["reload"]["generation_delta"] == 1

    assert abs(int(report["pixel_agreement"]["delta"][0])) <= 1
    assert abs(int(report["pixel_agreement"]["delta"][1])) <= 1
    assert report["pixel_agreement"]["measurement_method"] == "focus_list"

    assert report["rebinding"]["ok"] is True
    assert report["rebinding"]["widget_id"] == "imgbtn_target"

    assert report["byte_identical_undo"]["matches_baseline"] is True
    assert report["byte_identical_undo"]["patched_differed"] is True
