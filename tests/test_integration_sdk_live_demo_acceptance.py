"""Single-session live acceptance for the demo editor workflow."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_SDK_TESTS"),
    reason="set RENFORGE_SDK_TESTS=1 to run live Ren'Py SDK integration tests",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture(scope="module")
def sdk():
    from renforge.sdk import DEFAULT_RENPY_VERSION, get_or_install_sdk

    return get_or_install_sdk(os.environ.get("RENFORGE_SDK_VERSION", DEFAULT_RENPY_VERSION))


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    return destination


@pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="live bridge needs a display (set DISPLAY, or run under xvfb)",
)
def test_live_demo_editor_v1_acceptance(sdk, demo_copy: Path) -> None:
    pytest.importorskip("PIL.Image")

    from renforge.bridge.launcher import launch_with_bridge
    from renforge.editor_demo_runner import run_demo_v1_scenario
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90, editor=True) as session:
        report = run_demo_v1_scenario(session.client)

    assert report["take_analysis"]["selected_lock_reason"] is None
    assert report["take_analysis"]["current_analysis_id"]
    assert abs(report["drag_delta"][0]) >= 20
    assert abs(report["drag_delta"][1]) <= 1
    real_drag = report["real_mouse_drag"]
    assert real_drag["moved"] is True
    assert max(abs(real_drag["delta"][0]), abs(real_drag["delta"][1])) >= 20
    assert real_drag["screen_still_visible"] is True
    assert real_drag["choice_not_activated"] is True

    snap = report["snap"]
    assert isinstance(snap["guide_y"], int)
    assert snap["guide_y"] == report["decline_top"]
    assert snap["preview_on_anchor"] is True
    assert snap["guide_widget"] is True
    assert snap["distance_y_text"]
    assert snap["guide_snapshot"]["line_y"][1] == snap["guide_y"]
    assert 0 < snap["guide_length"] < 1280
    assert snap["guide_over_neighbour"] > 2.0
    assert snap["guide_opacity_delta"] > 0.0

    assert report["shift_drag"]["guide_x"] is None
    assert report["shift_drag"]["guide_y"] is None
    assert report["shift_nudge"]["delta"] == [10, 0]

    history = report["history"]
    h_base = report["history"]["baseline"]
    assert history["reset_ok"] is True
    assert history["nudge_preview"] == [h_base[0] + 1, h_base[1]]
    assert all(
        abs(history["undo_preview"][index] - h_base[index]) <= 1
        for index in range(2)
    )
    assert all(
        abs(history["redo_preview"][index] - history["nudge_preview"][index]) <= 1
        for index in range(2)
    )
    assert report["overlay_undo_ok"] is True
    assert report["overlay_redo_ok"] is True
    assert report["overlay_reset_ok"] is True
    assert all(
        abs(history["reset_preview"][index] - h_base[index]) <= 1
        for index in range(2)
    )

    locked = report["locked"]
    assert locked["selected_lock_reason"] not in (None, "", "ANALYZING")
    assert locked["save_enabled"] is False
    assert len(locked["observation_rect"]) == 4
    assert locked["lock_label_text"]
    assert locked["lock_code_in_label"] is True
    assert report["reset_after_save_error"] == "RESET_UNAVAILABLE"

    assert report["multi"]["dirty_before_undo"] >= 2
    assert report["multi"]["dirty_after_undo"] <= 1

    assert report["save1"]["status_text"] == "Reload committed"
    assert report["save2"]["status_text"] == "Reload committed"
    assert report["save2"]["script_generation"] > report["save1"]["script_generation"]


@pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="live bridge needs a display (set DISPLAY, or run under xvfb)",
)
def test_live_editor_controls_do_not_advance_the_story(sdk, demo_copy: Path) -> None:
    """An editor click must edit, never play the game underneath.

    `Function` ends the interaction as soon as its callable returns a non-None
    value, so every editor button used to dismiss the dialogue while doing its
    own job: pressing Exit also played several statements of the story.
    """
    import time

    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    def position(client) -> str:
        return client.eval_expr("str(renpy.get_filename_line())")

    def settle(client, *, stable_reads: int = 5, gap: float = 0.25, timeout: float = 30.0) -> str:
        """A script position that held still: transitions advance on their own."""
        deadline = time.monotonic() + timeout
        last = position(client)
        same = 1
        while time.monotonic() < deadline:
            time.sleep(gap)
            current = position(client)
            same = same + 1 if current == last else 1
            last = current
            if same >= stable_reads:
                return last
        raise AssertionError(f"script position never settled: {last}")

    def center(client, element_id: str) -> tuple[int, int]:
        for element in client.request("list_ui_elements", {}).get("elements", []):
            if element.get("id") == element_id:
                box = element["bounds"]
                return box["x"] + box["width"] // 2, box["y"] + box["height"] // 2
        raise AssertionError(f"control not on screen: {element_id}")

    def advanced(client, x: int, y: int) -> bool:
        before = settle(client)
        client.send_input(
            drag={"points": [[x, y], [x, y]], "button": 1, "coordinate_space": "logical"}
        )
        return settle(client) != before

    def wait_screen(client, name: str) -> bool:
        for _ in range(80):
            if client.inspect_screen(name).get("active") is True:
                return True
            time.sleep(0.1)
        return False

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90, editor=True) as session:
        client = session.client
        assert wait_screen(client, "_renforge_editor_launcher")

        # Control: with the editor closed, a posted click dismisses the say.
        # Without this, every "did not move" below would prove nothing.
        assert advanced(client, 600, 400) is True

        assert advanced(client, *center(client, "rf_launcher")) is False
        assert wait_screen(client, "_renforge_editor_overlay")

        assert advanced(client, 600, 400) is False
        assert advanced(client, *center(client, "rf_tools")) is False
        assert advanced(client, *center(client, "rf_exit")) is False

        # Leaving the editor must hand input back, not leave the game deaf.
        assert wait_screen(client, "_renforge_editor_launcher")
        assert advanced(client, 600, 400) is True
