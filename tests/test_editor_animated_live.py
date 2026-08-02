from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_animated_runner import (
    FIXTURE_SCREEN,
    inject_editor_animated_resources,
    run_editor_animated_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_ANIMATED_LIVE"),
    reason="set RENFORGE_ANIMATED_LIVE=1 to run issue #51 animated spike",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_animated_resources(destination)
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
            break
        time.sleep(0.05)
    else:
        pytest.fail("editor overlay never became active")

    for _ in range(20):
        if session.client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")') is True:
            break
        time.sleep(0.1)
    else:
        pytest.fail("animated fixture screen never became active")


def test_animated_element_editing_spike(demo_copy: Path) -> None:
    """Issue #51: Animated elements stay blocked under _widget_properties preview seam."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_animated_fixture.rpy"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = run_editor_animated_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    assert report["verdict"] == "blocked"
    assert report["reason_code"] in {"atl_position_override_conflict", "atl_time_reset"}
    assert "variants" in report
