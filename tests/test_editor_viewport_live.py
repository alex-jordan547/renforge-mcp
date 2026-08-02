from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_viewport_runner import (
    FIXTURE_SCREEN,
    inject_editor_viewport_resources,
    prove_scroll_tracking,
    run_editor_viewport_live_scenario,
    run_editor_viewport_scrolled_commit,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_VIEWPORT_LIVE"),
    reason="set RENFORGE_VIEWPORT_LIVE=1 to run the viewport ancestry proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_viewport_resources(destination)
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
        pytest.fail("viewport fixture screen never became available")


def test_viewport_seven_step_live_proof(demo_copy: Path) -> None:
    """The write chain holds for a target inside a viewport at its resting scroll."""
    scroll = 0
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_viewport_fixture.rpy"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = run_editor_viewport_live_scenario(
            session.client,
            fixture_path=fixture_path,
            scroll=scroll,
        )

    assert report["resolve"]["lock_reason"] in (None, "")
    assert report["resolve"]["move"] is True
    assert report["resolve"]["measurement_method"] == "focus_list"
    # The unlocked shape is exactly one viewport deep.
    assert report["resolve"]["viewport_ancestor_count"] == 1

    assert report["scroll"]["applied"] == pytest.approx(scroll, abs=1)

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

    # Other viewport shapes stay locked with their own reasons.
    locks = report["locks"]
    assert locks["computed"] == "YPOS_LITERAL_REQUIRED"
    assert locks["container"] == "CONTAINER_POSITION_UNSUPPORTED"
    assert locks["nested"] == "NESTED_VIEWPORT_UNSUPPORTED"

    undo = report["byte_identical_undo"]
    assert undo["matches_baseline"] is True
    assert undo["patched_differed"] is True


def test_focus_rects_track_viewport_scroll(demo_copy: Path) -> None:
    """The measurement the whole unlock rests on: focus rects include the scroll."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        session.client.request("editor_task0_start", {"screen": FIXTURE_SCREEN})
        report = prove_scroll_tracking(session.client, [0, 40, 90, 0])

    assert report["tracks_scroll"] is True
    # Returning to the original offset must reproduce the original rect.
    assert report["samples"][0]["rect_y"] == report["samples"][-1]["rect_y"]


def test_commit_while_scrolled_is_refused_without_touching_source(demo_copy: Path) -> None:
    """Ren'Py drops the viewport scroll on reload, so a scrolled commit cannot attest.

    The editor must refuse rather than accept a position it cannot reproduce,
    and must leave the author's file exactly as it found it.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_viewport_fixture.rpy"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = run_editor_viewport_scrolled_commit(
            session.client,
            fixture_path=fixture_path,
            scroll=120,
        )

    # The measurement behind the refusal.
    assert report["scroll_before"] == pytest.approx(120, abs=1)
    assert report["scroll_after"] == pytest.approx(0, abs=1)
    assert report["scroll_survived_reload"] is False

    # The editor reports a failure, because it cannot reproduce the geometry it
    # attested against.
    assert report["status_text"] == "Reload failed"
    assert report["save_error"] == "TARGET_POSITION_MISMATCH"

    # The author's file is restored before the failure is reported, rather than
    # left holding published bytes until the attestation timer fires.
    assert report["source_unchanged"] is True
    assert report["written_delta"] == {"x": 0, "y": 0}
