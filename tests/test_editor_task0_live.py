from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from renforge.editor_task0_runner import (
    FIXTURE_SCREEN,
    inject_editor_task0_resources,
    run_editor_task0_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_TASK0_LIVE"),
    reason="set RENFORGE_TASK0_LIVE=1 to run Task 0 live proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_task0_resources(destination)
    return destination


def test_task0_live_editor_prerequisite(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=120) as session:
        report = run_editor_task0_live_scenario(session.client)

    assert report["save_enabled"] is False
    assert report["top_select_widget"] == "task0_top"
    assert report["target_select_widget"] == "task0_target"
    assert report["clipped_lock"] == "CLIPPED_ANCESTRY_UNSUPPORTED"
    assert report["dupe_lock"] in {"MULTI_INSTANCE_UNSUPPORTED", "SYNTHETIC_WIDGET_ID"}
    assert report["multi_instance_lock"] == "MULTI_INSTANCE_UNSUPPORTED"
    assert report["unknown_ancestry_lock"] == "UNKNOWN_ANCESTRY_TYPE"

    nudge = report["nudge"]
    assert int(nudge["after_three"]["x"]) - int(nudge["before"]["x"]) == 3
    assert int(nudge["after_shift"]["x"]) - int(nudge["after_three"]["x"]) == -10

    observation = report["observation"]
    assert observation["measurement_method"] == "focus_list"
    assert observation["frame_id"] == report["observation_frame_external"]
    assert observation["runtime_key"]["screen"] == FIXTURE_SCREEN
    assert len(observation["runtime_key"]["ancestry"]) >= 1

    coordinator = report["coordinator"]
    assert coordinator["applied"]["worker_thread_id"] != coordinator["applied"]["applied_thread_id"]
    assert coordinator["applied"]["applied_thread_id"] == coordinator["queued"]["main_thread_id"]

    guides = report["guide_red"]
    assert guides["high"] > 0
    assert int(guides["swatch_high"][2]) > int(guides["swatch_low"][2])

    colors = report["rf_exit_colors_low_opacity"]
    border_sum = sum(int(channel) for channel in colors["border"])
    fill_sum = sum(int(channel) for channel in colors["fill"])
    assert abs(fill_sum - border_sum) >= 100

    label = report["label"]
    assert label["far_box"] is not None
    assert label["near_box"] is not None
    width, height = label["image_size"]
    for bbox in (label["far_box"], label["near_box"]):
        assert bbox[0] >= 0 and bbox[1] >= 0
        assert bbox[2] < width and bbox[3] < height
    assert float(label["far_green"]) < float(label["near_green"])

    post_exit = report["post_exit"]
    assert post_exit["click_after"] == post_exit["click_before"] + 1
