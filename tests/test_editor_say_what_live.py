"""Opt-in live tests for #81 say.what style position using Ren'Py 8.5.3.

Set RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 to run these tests.

These tests verify the full product path:
- Select say.what dialogue
- Unlock position_mode = style_gui_dialogue
- Preview move through a scoped `say.what` widget override
- Commit to gui.rpy (delta math)
- Reload and rebind
- Verify global scope (second dialogue line)
- Undo and redo (byte-identical)
- Variant lock (Demo stays locked)
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_live_common import DEMO_COPY_IGNORE
from renforge.editor_say_what_position_runner import (
    FIXTURE_SCREEN,
    TARGET_ID,
    run_editor_say_what_style_position_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE"),
    reason="set RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 to run #81 say.what style position live gate",
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "say_what_clean"


@pytest.fixture
def clean_fixture_copy(tmp_path: Path) -> Path:
    """Copy clean say.what fixture without variant overrides."""
    destination = tmp_path / "say_what_clean"
    shutil.copytree(_FIXTURE, destination, dirs_exist_ok=True)
    return destination


def _open_editor(session) -> None:
    """Open RenForge editor and wait for overlay."""
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
        time.sleep(0.2)
    else:
        pytest.fail("editor launcher never became active")

    assert (
        session.client.click_element(
            text="RF",
            exact=True,
            screen="_renforge_editor_launcher",
        ).get("ok")
        is True
    )
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_overlay").get("active") is True:
            return
        time.sleep(0.05)
    pytest.fail("editor overlay never became active")


def test_say_what_style_position_live_product_path_pass(clean_fixture_copy: Path) -> None:
    """Full #81 live scenario: select, unlock, preview, save, reload, undo, redo.

    Acceptance criteria:
    - say.what unlocks with position_mode = style_gui_dialogue
    - Preview mutates style, doesn't rebuild say screen (no TypeError)
    - Commit writes delta to gui.rpy (not absolute screen coords)
    - screens.rpy unchanged (identity path)
    - Reload + rebind succeeds with geometry ≤1px
    - Undo is byte-identical
    - Redo reapplies byte-identical source and geometry
    """
    try:
        from renforge.bridge.launcher import launch_with_bridge
        from renforge.project import RenpyProject
        from renforge.sdk import get_or_install_sdk
    except ImportError as exc:
        pytest.skip(f"Ren'Py runtime not available: {exc}")

    sdk = get_or_install_sdk("8.5.3", project_root=clean_fixture_copy)
    screens_path = clean_fixture_copy / "game" / "screens.rpy"
    gui_path = clean_fixture_copy / "game" / "gui.rpy"
    screenshot_path = clean_fixture_copy / ".renforge" / "say-what-live.png"

    with launch_with_bridge(
        sdk,
        RenpyProject(clean_fixture_copy),
        startup_timeout=120,
        editor=True,
    ) as session:
        session.client.eval_expr(
            'renpy.show_screen("say", who=None, '
            'what="Test dialogue for RenForge #81", _layer="screens")'
        )
        _open_editor(session)

        # Run full scenario
        report = run_editor_say_what_style_position_live_scenario(
            session.client,
            fixture_path=screens_path,
            gui_path=gui_path,
            screenshot_path=screenshot_path,
        )

    # Assert acceptance criteria
    assert report.get("move_unlocked") is True, report
    assert report.get("preview_source_unchanged") is True, report
    assert report["preview_geometry_correct"] is True, report
    assert report["preview_preserved_dialogue"] is True, report
    assert report.get("gui_source_changed") is True, report
    assert report["delta_correct"] is True, "commit must apply logical-pixel delta, not absolute screen coords"
    assert report["undo_byte_identical"] is True, "undo must restore byte-identical gui.rpy"
    assert report["redo_byte_identical"] is True, "redo must reapply byte-identical gui.rpy"
    assert screenshot_path.is_file(), "live evidence screenshot must be captured"
    assert report["verdict"] == "pass", f"Live scenario failed: {report}"


def test_say_what_variant_demo_stays_locked(clean_fixture_copy: Path) -> None:
    """Verify a project with @gui.variant small() override stays locked.

    Expected:
    - capabilities["move"] = False
    - lock_code = STYLE_POSITION_VARIANT_UNSUPPORTED
    - NOT XPOS_DUPLICATE
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    gui_path = clean_fixture_copy / "game" / "gui.rpy"
    with gui_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\ninit python:\n"
            "    @gui.variant\n"
            "    def small():\n"
            "        gui.dialogue_xpos = gui.scale(90)\n"
        )

    sdk = get_or_install_sdk("8.5.3", project_root=clean_fixture_copy)
    with launch_with_bridge(
        sdk,
        RenpyProject(clean_fixture_copy),
        startup_timeout=120,
        editor=True,
    ) as session:
        session.client.eval_expr(
            'renpy.show_screen("say", who=None, '
            'what="Variant dialogue for RenForge #81", _layer="screens")'
        )
        _open_editor(session)
        nodes = session.client.scene_tree(types=["text"], detail="semantic").get("nodes") or []
        target = next(
            node
            for node in nodes
            if "Variant dialogue for RenForge #81" in str(node.get("text") or "")
        )
        bounds = target["bounds"]
        select_result = session.client.eval_expr(
            f'_renforge_editor_select({int(bounds["x"]) + 10}, {int(bounds["y"]) + 10})'
        )
        for _ in range(100):
            status = session.client.request("editor_task0_status", {})
            if (
                status.get("status_code") == "locked"
                and status.get("selected_lock_reason") != "ANALYZING"
            ):
                break
            time.sleep(0.1)

    assert status["capabilities"]["move"] is False, (select_result, status)
    assert status["selected_lock_reason"] == "STYLE_POSITION_VARIANT_UNSUPPORTED", (
        select_result,
        status,
    )


# Run with:
# RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 pytest tests/test_editor_say_what_live.py -v
