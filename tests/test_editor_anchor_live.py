from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_live_common import DEMO_COPY_IGNORE
from renforge.editor_anchor_runner import (
    FIXTURE_SCREEN,
    inject_editor_anchor_resources,
    run_editor_anchor_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_ANCHOR_LIVE"),
    reason="set RENFORGE_ANCHOR_LIVE=1 to run anchor-aware seven-step live proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=DEMO_COPY_IGNORE)
    inject_editor_anchor_resources(destination)
    return destination


def test_anchor_seven_step_live_proof(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_anchor_fixture.rpy"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
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
            pytest.fail("anchor fixture screen never became available")

        report = run_editor_anchor_live_scenario(session.client, fixture_path=fixture_path)

    assert report["resolve"]["statement_kind"] == "textbutton"
    assert report["resolve"]["move"] is True
    assert report["fixture_before"]["has_anchor"] is True
    preview = report["preview"]
    assert preview["bounds_before"] != preview["bounds_after"]
    assert all(
        abs(preview["observed_delta"][axis] - preview["requested_delta"][axis]) <= 1
        for axis in (0, 1)
    )
    patch = report["patch"]
    assert patch["outside_coordinate_spans_identical"] is True
    assert patch["matches_independent_expected"] is True
    assert report["reload"].get("status_code") == "reload_committed" or report["reload"].get("status_code") == "reload_committed"
    assert all(abs(int(v)) <= 1 for v in report["pixel_agreement"]["delta"])
    assert report["value_invariance"]["preview"] == report["value_invariance"]["baseline"]
    assert report["rebinding"]["widget_id"] == "anchor_target"
    locks = report["locks"]
    assert locks["computed"] == "XPOS_LITERAL_REQUIRED"
    assert locks["container"] == "CONTAINER_POSITION_UNSUPPORTED"
    assert locks["ambiguous"] == "REPEATED_USE_UNSUPPORTED"
    assert locks["unproven"] == "UNKNOWN_ANCESTRY_TYPE"
    assert report["byte_identical_undo"]["matches_baseline"] is True
