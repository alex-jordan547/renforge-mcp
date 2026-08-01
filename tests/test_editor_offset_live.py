from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_offset_runner import (
    FIXTURE_SCREEN,
    inject_editor_offset_resources,
    run_editor_offset_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_OFFSET_LIVE"),
    reason="set RENFORGE_OFFSET_LIVE=1 to run offset (x, y) seven-step live proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_offset_resources(destination)
    return destination


def test_offset_seven_step_live_proof(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_offset_fixture.rpy"

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
            pytest.fail("offset fixture screen never became available")

        report = run_editor_offset_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    assert report["resolve"]["statement_kind"] == "textbutton"
    assert report["resolve"]["lock_reason"] in (None, "")
    assert report["resolve"]["move"] is True
    assert report["resolve"]["measurement_method"] == "focus_list"
    assert report["fixture_before"]["position_mode"] == "offset"

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
    # Offset write-back is authored + Δruntime, not absolute runtime coordinates.
    assert patch["source_position_after"] == patch["expected_source_position"]

    assert report["reload"]["ok"] is True
    assert report["reload"]["status_text"] == "Reload committed"
    assert report["reload"]["generation_delta"] == 1

    assert all(abs(int(value)) <= 1 for value in report["pixel_agreement"]["delta"])

    value_invariance = report["value_invariance"]
    assert value_invariance["preview"] == value_invariance["baseline"]
    assert value_invariance["reload"] == value_invariance["baseline"]

    assert report["rebinding"]["ok"] is True
    assert report["rebinding"]["widget_id"] == "offset_target"

    locks = report["locks"]
    assert locks["computed"] == "OFFSET_LITERAL_REQUIRED"
    assert locks["container"] == "CONTAINER_POSITION_UNSUPPORTED"
    assert locks["ambiguous"] == "SYNTHETIC_WIDGET_ID"
    assert locks["unproven"] == "UNKNOWN_ANCESTRY_TYPE"

    undo = report["byte_identical_undo"]
    assert undo["matches_baseline"] is True
    assert undo["patched_differed"] is True
