from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_bar_resize_runner import run_editor_bar_resize_live_scenario
from renforge.editor_bar_runner import FIXTURE_SCREEN, inject_editor_bar_resources

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_BAR_RESIZE_LIVE"),
    reason="set RENFORGE_BAR_RESIZE_LIVE=1 to run bar resize seven-step live proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_bar_resources(destination)
    return destination


def test_bar_resize_seven_step_live_proof(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_bar_fixture.rpy"

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
            pytest.fail("bar fixture screen never became available")

        report = run_editor_bar_resize_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    locks = report["locks"]
    assert locks["xysize"]["code"] == "BAR_XYSIZE_UNSUPPORTED"
    assert locks["xysize"]["move"] is True
    assert locks["xysize"]["resize"] is False
    assert locks["constraint"]["code"] == "BAR_SIZE_CONSTRAINT_UNSUPPORTED"
    assert locks["constraint"]["move"] is True
    assert locks["constraint"]["resize"] is False

    assert report["resolve"]["statement_kind"] == "bar"
    assert report["resolve"]["size_mode"] == "xsize_ysize"
    assert report["resolve"]["lock_reason"] in (None, "")
    assert report["resolve"]["resize"] is True
    assert report["resolve"]["move"] is True
    assert report["resolve"]["measurement_method"] == "focus_list"
    assert report["resolve"]["frame_id"]

    preview = report["preview"]
    assert preview["bounds_before"] != preview["bounds_after"]
    assert all(
        abs(int(preview["observed_delta"][i]) - int(preview["requested_delta"][i])) <= 1
        for i in (0, 1)
    )
    assert preview["measurement_method"] == "focus_list"
    assert preview["frame_id_before"] != preview["frame_id_after"]

    patch = report["patch"]
    assert patch["outside_size_spans_identical"] is True
    assert patch["matches_independent_expected"] is True
    assert patch["after_sha256"] != patch["before_sha256"]
    assert patch["source_size_after"] == {
        "w": preview["requested_after"][0],
        "h": preview["requested_after"][1],
    }

    assert report["reload"]["ok"] is True
    assert report["reload"]["status_text"] == "Reload committed"
    assert report["reload"]["generation_delta"] == 1
    assert report["reload"]["frame_id"]

    assert all(abs(int(value)) <= 1 for value in report["pixel_agreement"]["delta"])
    assert report["pixel_agreement"]["measurement_method"] == "focus_list"

    assert report["rebinding"]["ok"] is True
    assert report["rebinding"]["widget_id"] == "bar_target"
    assert (report["rebinding"]["source_key"] or {}).get("size_mode") == "xsize_ysize"

    undo = report["byte_identical_undo"]
    assert undo["matches_baseline"] is True
    assert undo["patched_differed"] is True
