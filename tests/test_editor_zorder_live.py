from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_zorder_runner import (
    FIXTURE_SCREEN,
    SIBLING_ID,
    TARGET_ID,
    inject_editor_zorder_resources,
    run_editor_zorder_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_ZORDER_LIVE"),
    reason="set RENFORGE_ZORDER_LIVE=1 to run issue #49 z-order spike",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_zorder_resources(destination)
    return destination


def _open_editor(session) -> None:
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
        time.sleep(0.2)
    else:
        pytest.fail("editor launcher never became active")

    assert session.client.click_element(
        text="RF", exact=True, screen="_renforge_editor_launcher"
    ).get("ok") is True
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_overlay").get("active") is True:
            return
        time.sleep(0.05)
    pytest.fail("editor overlay never became active")


def test_zorder_source_swap_is_live_but_product_remains_blocked(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_zorder_fixture.rpy"

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = run_editor_zorder_live_scenario(session.client, fixture_path=fixture_path)

    assert report["before"]["dominant"] == "blue", report
    assert report["before"]["selected_widget_id"] == SIBLING_ID
    assert report["after_reload"]["dominant"] == "red"
    assert report["after_reload"]["selected_widget_id"] == TARGET_ID
    assert report["runtime_result_proven"] is True
    assert report["stable_rebind"] is True
    assert report["after_reload"]["runtime_source_locations"][TARGET_ID][1] == report[
        "source_patch"
    ]["locations"][TARGET_ID]
    assert report["after_reload"]["runtime_source_locations"][SIBLING_ID][1] == report[
        "source_patch"
    ]["locations"][SIBLING_ID]
    assert report["source_patch"]["changed"] is True
    assert report["source_patch"]["size_delta"] == 0
    assert report["restore"]["byte_identical"] is True
    assert report["restore"]["sha256"] == report["baseline_sha256"]
    assert report["verdict"] == "blocked"
    assert report["verdict_reason"] == "structural_transaction_undo_missing"
    assert FIXTURE_SCREEN == "renforge_editor_zorder_fixture"
