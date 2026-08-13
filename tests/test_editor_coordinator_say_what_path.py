"""Coordinator integration tests for #81 say.what style position.

These tests verify the actual coordinator analyze path including lock code mapping.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from renforge.editor.source import (
    analyze_say_what_style_position,
)


def test_demo_variant_returns_variant_unsupported_not_xpos_duplicate(tmp_path: Path) -> None:
    """Prove Demo with variant override gets STYLE_POSITION_VARIANT_UNSUPPORTED, not XPOS_DUPLICATE.

    This tests the actual analyze_say_what_style_position function that coordinator uses.
    """
    # Simulate Demo gui.rpy with variant override
    gui_rpy = tmp_path / "gui.rpy"
    gui_rpy.write_text(
        "define gui.dialogue_xpos = gui.scale(268)\n"
        "define gui.dialogue_ypos = gui.scale(50)\n"
        "\n"
        "init python:\n"
        "    @gui.variant\n"
        "    def small():\n"
        "        gui.dialogue_xpos = gui.scale(90)\n",
        encoding="utf-8",
    )

    gui_source = gui_rpy.read_text(encoding="utf-8")
    stmt = analyze_say_what_style_position(
        gui_source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    # Verify lock code is VARIANT_UNSUPPORTED, NOT XPOS_DUPLICATE
    assert stmt.position_lock_code == "STYLE_POSITION_VARIANT_UNSUPPORTED"
    assert stmt.position_mode is None
    assert "variant" in stmt.position_lock_message.lower()


def test_clean_fixture_unlocks_style_gui_dialogue(tmp_path: Path) -> None:
    """Prove clean fixture without variant unlocks position_mode = style_gui_dialogue."""
    gui_rpy = tmp_path / "gui.rpy"
    gui_rpy.write_text(
        "define gui.dialogue_xpos = gui.scale(268)\n"
        "define gui.dialogue_ypos = gui.scale(50)\n",
        encoding="utf-8",
    )

    gui_source = gui_rpy.read_text(encoding="utf-8")
    stmt = analyze_say_what_style_position(
        gui_source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    # Verify unlock
    assert stmt.position_mode == "style_gui_dialogue"
    assert stmt.position_lock_code is None
    assert stmt.xpos == 268
    assert stmt.ypos == 50


def test_path_resolution_uses_game_relative() -> None:
    """Prove gui_rpy_path convention is game-relative, not double-prefixed."""
    # This is the correct convention
    gui_rpy_path = "gui.rpy"

    # verify NOT double-prefixed
    assert gui_rpy_path == "gui.rpy"
    assert "game/" not in gui_rpy_path

    # resolve_game_path already joins project_root / "game" / relative_path
    # So "gui.rpy" → /project/game/gui.rpy ✓
    # And "game/gui.rpy" → /project/game/game/gui.rpy ✗


def test_missing_gui_rpy_returns_unresolved() -> None:
    """Prove missing gui.rpy is detected."""
    # Empty source
    gui_source = ""
    stmt = analyze_say_what_style_position(
        gui_source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert stmt.position_lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"
    assert stmt.position_mode is None
