from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_animated_runner import (
    FIXTURE_SCREEN,
    _show_fixture,
    inject_editor_animated_resources,
    run_editor_animated_live_scenario,
)
from renforge.editor_live_common import wait_bounds

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


def test_atl_ancestry_reports_its_own_lock_reason(demo_copy: Path) -> None:
    """Issue #51: an ATL ancestor is locked as animated, not as an unknown type.

    Both targets sit still on screen: `anim_style_target` animates alpha only and
    `anim_static_transform` is a stationary wrapper, so the click point is stable.
    `anim_pos_target` is deliberately not probed here — its ATL moves it between
    the bounds read and the click.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)

    def select_center(client, widget_id: str) -> dict:
        bounds = wait_bounds(client, widget_id, fixture_screen=FIXTURE_SCREEN)
        return client.request(
            "editor_task0_select",
            {
                "x": bounds["x"] + bounds["width"] // 2,
                "y": bounds["y"] + bounds["height"] // 2,
            },
        )

    with launch_with_bridge(
        sdk, RenpyProject(demo_copy), startup_timeout=120, editor=True
    ) as session:
        _open_editor(session)
        _show_fixture(session.client)
        animated = select_center(session.client, "anim_style_target")
        stationary = select_center(session.client, "anim_static_transform")

    # An active ATL stays locked, but says why.
    assert animated.get("ok") is False
    assert animated.get("lock_reason") == "ATL_ANIMATION_UNSUPPORTED"

    # A stationary Transform wrapper keeps working: this lock must not widen.
    assert stationary.get("ok") is True
    assert stationary.get("lock_reason") is None
    assert (stationary.get("selected") or {}).get("widget_id") == "anim_static_transform"
