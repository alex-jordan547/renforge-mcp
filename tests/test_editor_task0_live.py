from __future__ import annotations

import os
import shutil
import time
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
    from renforge.util.subprocess import run_command

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    lint = run_command(project.lint_command(sdk), timeout=180)
    assert "lint report" in lint.stdout.lower(), lint.stdout + lint.stderr
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
        launcher_status = session.client.request("editor_task0_status")
        assert launcher_status.get("active") is True, launcher_status
        assert session.client.eval_expr("_renforge_editor_label_snapshot()") is None
        assert session.client.request(
            "editor_task0_key",
            {"key": "escape", "repeat": 1},
        ).get("ok") is True

        report = run_editor_task0_live_scenario(
            session.client,
            fixture_path=demo_copy / "game" / "zz_renforge_editor_task0_fixture.rpy",
        )

    assert report["save_enabled"] is False
    assert report["top_select_widget"] == "task0_top"
    assert report["target_select_widget"] == "task0_target"
    assert report["clipped_lock"] == "CLIPPED_ANCESTRY_UNSUPPORTED"
    assert report["dupe_lock"] == "REPEATED_USE_UNSUPPORTED"
    assert report["multi_instance_lock"] == "MULTI_INSTANCE_UNSUPPORTED"
    assert report["unique_rebind"]["ok"] is True
    assert report["unique_rebind"]["state"] == "all_targets_attested"
    assert report["unknown_ancestry_lock"] == "UNKNOWN_ANCESTRY_TYPE"
    analyzed_label = report["label_after_analysis"]
    assert isinstance(analyzed_label, dict)
    assert "[ANALYZING]" not in analyzed_label["text"]
    assert report["no_op_save"] == {"ok": False, "error": "SAVE_UNAVAILABLE"}

    snap_samples = report["drag_snap"]["samples"]
    assert snap_samples[1]["guide_x"] == 360, snap_samples
    assert snap_samples[2]["guide_x"] == 360
    assert snap_samples[1]["preview_position"][0] == snap_samples[2]["preview_position"][0]
    assert snap_samples[3]["guide_x"] is None
    assert report["drag_snap"]["event_method"] == "_renforge_editor_handle_event"
    assert report["drag_snap"]["drag_active_before_mouse_up"] is True
    assert report["drag_snap"]["preview_before_mouse_up"] == snap_samples[-1]["preview_position"]
    assert report["drag_snap"]["preview_before_mouse_up"] != snap_samples[0]["preview_position"]
    motion_drag = report["motion_drag"]
    assert motion_drag["after"]["preview"] != motion_drag["base"]
    assert motion_drag["after"]["drag_active"] is True
    assert set(motion_drag["after"]["measure"]) == {"dx", "dy"}
    assert motion_drag["after"]["measure_x"] is False
    assert motion_drag["after"]["measure_y"] is False
    assert motion_drag["after"]["guide"] == {"line_x": None, "line_y": None}
    distance_badge = report["distance_badge"]
    assert isinstance(distance_badge, dict)
    assert int(distance_badge["delta_x"]) != 0
    assert distance_badge["text_x"].endswith(" px")
    rendered_distance = report["distance_badge_rendered_text"]
    assert isinstance(rendered_distance, str)
    assert distance_badge["text_x"] in rendered_distance
    guide_snapshot = report["guide_snapshot"]
    assert guide_snapshot["line_x"] is not None
    assert guide_snapshot["line_y"] is not None
    assert 0 < guide_snapshot["line_x"][2] < 720
    assert 0 < guide_snapshot["line_y"][2] < 1280
    assert report["guide_after_mouse_up"] == {"line_x": None, "line_y": None}
    tools_visibility = report["tools_visibility"]
    assert tools_visibility["hide_click"]["ok"] is True
    assert tools_visibility["hidden_state"] == [False, True, False, False, False, True]
    assert tools_visibility["show_click"]["ok"] is True
    assert tools_visibility["restored_widget"] is True

    nudge = report["nudge"]
    assert int(nudge["after_three"]["x"]) - int(nudge["before"]["x"]) == 3
    assert int(nudge["after_shift"]["x"]) - int(nudge["after_three"]["x"]) == -10

    observation = report["observation"]
    assert observation["measurement_method"] == "focus_list"
    assert observation["frame_id"].split(":", 1)[0] == report["observation_frame_external"]
    assert observation["runtime_key"]["screen"] == FIXTURE_SCREEN
    assert len(observation["runtime_key"]["ancestry"]) >= 1
    assert report["attestation"]["ok"] is True

    coordinator = report["coordinator"]
    assert coordinator["applied"]["worker_thread_id"] != coordinator["applied"]["applied_thread_id"]
    assert coordinator["applied"]["applied_thread_id"] == coordinator["queued"]["main_thread_id"]

    assert report["save_status"]["save_in_progress"] is False
    assert report["save_status"].get("save_error") is None
    assert report["save_request"]["ok"] is True
    assert report["save_control_labels"] == {
        "saving": "Saving / Reloading...",
        "saved": "Saved",
    }
    assert report["post_save_source"]["sha256"] != report["fixture_before"]["sha256"]
    assert abs(int(report["pre_save_target"]["x"]) - int(report["post_save_target"]["x"])) <= 1
    assert abs(int(report["pre_save_target"]["y"]) - int(report["post_save_target"]["y"])) <= 1
    assert report["pre_save_source"]["position"] != report["post_save_source"]["position"]
    assert report["post_save_source"]["positions"]["task0_top"] != report["fixture_before"]["positions"]["task0_top"]
    assert report["reset_after_save"] == {"ok": False, "error": "RESET_UNAVAILABLE"}
    assert report["successor_analysis"]["current_analysis_id"] != report["save_status"].get("current_analysis_id")

    multi_target = report["multi_target"]
    assert int(multi_target["top_after"]["x"]) - int(multi_target["top_before"]["x"]) == 10
    assert multi_target["target_after_reselect"] == multi_target["target_before"]
    assert multi_target["top_after_undo"] == multi_target["top_before"]
    assert multi_target["target_after_undo"] == multi_target["target_before"]
    assert multi_target["top_after_redo"] == multi_target["top_after"]
    assert multi_target["target_after_redo"] == multi_target["target_before"]

    second_save = report["second_save"]
    assert second_save["request"]["ok"] is True
    assert second_save["status"]["script_generation"] == report["save_status"]["script_generation"] + 1
    assert int(second_save["target_after"]["x"]) - int(second_save["target_before"]["x"]) == 2
    assert second_save["source_after"]["sha256"] != second_save["source_before"]["sha256"]
    assert second_save["successor"]["current_analysis_id"] != report["successor_analysis"]["current_analysis_id"]
    initial_target_source = report["fixture_before"]["positions"]["task0_target"]
    first_target_source = report["post_save_source"]["positions"]["task0_target"]
    assert (
        int(first_target_source["x"]) - int(initial_target_source["x"])
        == int(report["post_save_target"]["x"]) - int(report["target_before"]["x"])
    )
    assert (
        int(first_target_source["y"]) - int(initial_target_source["y"])
        == int(report["post_save_target"]["y"]) - int(report["target_before"]["y"])
    )

    guides = report["guide_red"]
    assert guides["high"] > 0
    swatch_distance = sum(
        abs(int(high) - int(low))
        for high, low in zip(guides["swatch_high"], guides["swatch_low"], strict=True)
    )
    assert swatch_distance >= 20

    exit_colors = report["rf_exit_colors_low_opacity"]
    exit_border = tuple(int(channel) for channel in exit_colors["border"])
    exit_fill = tuple(int(channel) for channel in exit_colors["fill"])
    assert exit_border[2] >= 220
    assert exit_border[2] - exit_border[0] >= 60
    assert sum(
        abs(border_channel - fill_channel)
        for border_channel, fill_channel in zip(exit_border, exit_fill, strict=True)
    ) >= 100


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

    history = report["history"]
    assert history["undo_return"]["ok"] is True
    assert history["redo_return"]["ok"] is True

    assert not (demo_copy / "game" / "zz_renforge_editor_task0.rpyc.bak").exists()
    assert not (demo_copy / "game" / "zz_renforge_editor_task0_fixture.rpyc.bak").exists()
