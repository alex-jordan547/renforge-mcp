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
