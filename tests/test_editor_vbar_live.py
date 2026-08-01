from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_vbar_runner import (
    FIXTURE_SCREEN,
    inject_editor_vbar_resources,
    run_editor_vbar_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_VBAR_LIVE"),
    reason="set RENFORGE_VBAR_LIVE=1 to run vbar seven-step live proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_vbar_resources(destination)
    return destination


def test_vbar_seven_step_live_proof(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_vbar_fixture.rpy"

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
            pytest.fail("editor launcher never became active")

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
            pytest.fail("editor overlay never became active")

        for _ in range(20):
            available = session.client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")')
            if available is True:
                break
            time.sleep(0.1)
        else:
            pytest.fail("vbar fixture screen never became available")

        report = run_editor_vbar_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    assert report["resolve"]["statement_kind"] == "vbar"
    assert report["resolve"]["lock_reason"] in (None, "")
    assert report["resolve"]["move"] is True
    assert report["resolve"]["measurement_method"] == "focus_list"
    assert report["resolve"]["frame_id"]

    preview = report["preview"]
    assert preview["bounds_before"] != preview["bounds_after"]
    assert all(
        abs(preview["observed_delta"][axis] - preview["requested_delta"][axis]) <= 1
        for axis in (0, 1)
    )
    assert preview["measurement_method"] == "focus_list"
    assert preview["measurement_method_before"] == "focus_list"
    assert preview["measurement_method_after"] == "focus_list"
    assert preview["frame_id_before"]
    assert preview["frame_id_after"]
    assert preview["frame_id_before"] != preview["frame_id_after"]

    patch = report["patch"]
    assert patch["outside_coordinate_spans_identical"] is True
    assert patch["matches_independent_expected"] is True
    assert patch["after_sha256"] != patch["before_sha256"]
    assert patch["source_position_after"] == {
        "x": preview["requested_after"][0],
        "y": preview["requested_after"][1],
    }

    assert report["reload"]["ok"] is True
    assert report["reload"]["status_text"] == "Reload committed"
    assert report["reload"]["generation_delta"] == 1
    assert report["reload"]["frame_id"]

    assert all(abs(int(value)) <= 1 for value in report["pixel_agreement"]["delta"])
    assert report["pixel_agreement"]["measurement_method"] == "focus_list"
    assert report["pixel_agreement"]["measurement_method_preview"] == "focus_list"
    assert report["pixel_agreement"]["measurement_method_reload"] == "focus_list"
    assert report["pixel_agreement"]["frame_id_preview"]
    assert report["pixel_agreement"]["frame_id_reload"]
    assert report["pixel_agreement"]["frame_id_preview"] != report["pixel_agreement"]["frame_id_reload"]

    value_invariance = report["value_invariance"]
    assert value_invariance["preview"] == value_invariance["baseline"]
    assert value_invariance["reload"] == value_invariance["baseline"]

    assert report["rebinding"]["ok"] is True
    assert report["rebinding"]["widget_id"] == "vbar_target"
    assert (report["rebinding"]["source_key"] or {}).get("statement_kind") == "vbar"

    locks = report["locks"]
    assert locks["computed"] == "XPOS_LITERAL_REQUIRED"
    assert locks["style"] == "BAR_STYLE_POSITION_UNSUPPORTED"
    assert locks["missing_position"] == "BAR_POSITION_NOT_DIRECTLY_AUTHORED"
    assert locks["container"] == "CONTAINER_POSITION_UNSUPPORTED"
    assert locks["ambiguous"] == "SYNTHETIC_WIDGET_ID"
    assert locks["unproven"] == "UNKNOWN_ANCESTRY_TYPE"

    undo = report["byte_identical_undo"]
    assert undo["matches_baseline"] is True
    assert undo["patched_differed"] is True
