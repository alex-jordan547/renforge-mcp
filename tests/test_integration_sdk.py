"""Live Ren'Py SDK integration tests.

Opt-in: these download/use a real Ren'Py SDK and invoke it. Enable with::

    RENFORGE_SDK_TESTS=1 pytest tests/test_integration_sdk.py

Optionally pin the version with ``RENFORGE_SDK_VERSION`` (default: 8.3.7).
Each test runs against a temp copy of the demo so the committed one is never
polluted with compiled ``.rpyc``/cache artifacts.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_SDK_TESTS"),
    reason="set RENFORGE_SDK_TESTS=1 to run live Ren'Py SDK integration tests",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture(scope="module")
def sdk():
    from renforge.sdk import get_or_install_sdk

    return get_or_install_sdk(os.environ.get("RENFORGE_SDK_VERSION", "8.3.7"))


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination)
    return destination


def test_lint_demo_is_clean(sdk, demo_copy: Path) -> None:
    from renforge.project import RenpyProject
    from renforge.util.subprocess import run_command

    project = RenpyProject(demo_copy)
    result = run_command(project.lint_command(sdk), timeout=180)

    assert "lint report" in result.stdout.lower(), result.stdout + result.stderr


def test_native_dump_returns_authoritative_labels(sdk, demo_copy: Path) -> None:
    from renforge.dump import normalize_definitions, run_native_dump
    from renforge.project import RenpyProject

    raw = run_native_dump(sdk, RenpyProject(demo_copy), timeout=180)
    labels = {d["name"] for d in normalize_definitions(raw) if d["kind"] == "label"}

    assert {"start", "choice", "good", "bad"} <= labels


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_bridge_ping_state_and_screenshot(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        assert session.client.ping().get("pong") is True

        session.client.set_var("renforge_seen", "by-test")
        assert session.client.get_var("renforge_seen") == "by-test"

        state = session.client.get_state()
        assert "variables" in state and "current_label" in state

        png = session.client.screenshot()
        assert png.startswith(b"\x89PNG") and len(png) > 1000

        # Driving: the game starts at label "start" showing its first line;
        # advancing should let us capture dialogue via pushed events.
        says = []
        for _ in range(6):
            for event in session.client.poll_events().get("events", []):
                if event["type"] == "say":
                    says.append(event["what"])
            session.client.advance()
            time.sleep(1.0)
        assert any("Ren'Forge" in s for s in says), says


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_menu_selection_takes_the_branch(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        # Advance until the menu's choices appear on screen.
        choices = []
        for _ in range(6):
            choices = session.client.list_choices()
            if choices:
                break
            session.client.advance()
            time.sleep(1.0)
        assert any("lumineuse" in c["text"] for c in choices), choices

        session.client.select_choice(text="lumineuse")
        time.sleep(1.5)

        assert session.client.get_var("renforge_choice") == "good"
        assert session.client.get_state()["current_label"] == "good"


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="autopilot needs a display (set DISPLAY, or run under xvfb)")
def test_autopilot_covers_all_labels(sdk, demo_copy: Path) -> None:
    from renforge.autopilot import autopilot
    from renforge.project import RenpyProject

    report = autopilot(sdk, RenpyProject(demo_copy), max_runs=8, max_steps=30, settle=0.5)

    assert report["ok"] is True
    assert report["coverage"] == 1.0
    assert report["labels_unreached"] == []
    assert report["crashes"] == []
    assert report["choices_explored"] >= 2  # both branches taken


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_displayable_bounds_and_repositioning(sdk, demo_copy: Path) -> None:
    """Exercise the pixel-perfect tools against a real engine.

    Unit tests use a fake ``renpy``; this proves the real
    ``renpy.get_image_bounds`` and ``renpy.show(at_list=[Transform])`` behave as
    the bridge assumes, and that the image overlays/diff run on real frames.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.image_ops import annotate_png, diff_images
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client

        # Advance until the "wisp" sprite is actually on screen.
        for _ in range(8):
            if "wisp" in client.get_state().get("showing_tags", []):
                break
            client.advance()
            time.sleep(1.0)
        assert "wisp" in client.get_state()["showing_tags"], "wisp sprite never shown"

        # 1) get_displayable_bounds returns a real logical rectangle.
        measured = client.get_displayable_bounds("wisp")
        assert measured["ok"] is True, measured
        assert measured["coordinate_space"] == "logical"
        start = measured["bounds"]
        assert start["width"] > 0 and start["height"] > 0, measured

        # 2) overlay + diff run on genuine PNG frames of the same size.
        before_png = client.screenshot()
        overlaid = annotate_png(before_png, grid=100, rulers=True, crosshair=(start["x"], start["y"]))
        assert overlaid.startswith(b"\x89PNG") and len(overlaid) > 1000

        # 3) position_element moves the sprite; bounds and the frame both change.
        target_x = start["x"] + 200
        moved = client.position_element("wisp", xpos=target_x, xanchor=0.0, ypos=start["y"], yanchor=0.0)
        assert moved["ok"] is True, moved
        assert moved["bounds"]["x"] != start["x"], (start, moved["bounds"])
        assert abs(moved["bounds"]["x"] - target_x) <= 2, moved

        # 4) the reposition is measurable frame-to-frame.
        after_png = client.screenshot()
        diff = diff_images(before_png, after_png, threshold=16)
        assert diff["changed"] is True, diff
        assert diff["bounds"] is not None, diff
