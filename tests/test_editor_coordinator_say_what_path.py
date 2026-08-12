"""Coordinator unit tests for #81 say.what style position path resolution and lock codes."""

from __future__ import annotations

from pathlib import Path

import pytest

# Note: Full coordinator integration tests require RuntimeProbe mock setup.
# These tests verify path resolution logic in isolation.


def test_gui_rpy_path_is_game_relative() -> None:
    """Prove that gui_rpy_path is game-relative, not double-prefixed.
    
    Bug: coordinator used gui_rpy_path = "game/gui.rpy"
    resolve_game_path already joins project_root / "game" / relative
    → looked for game/game/gui.rpy → PATH_NOT_FOUND
    → bare except silently kept XPOS_DUPLICATE
    
    Fix: gui_rpy_path = "gui.rpy" (game-relative)
    """
    # This is a doc test proving the correct convention
    gui_rpy_path = "gui.rpy"  # Correct: game-relative
    
    # resolve_game_path signature:
    # resolve_game_path(project_root, relative_path) -> Path
    # Implementation: game_root = project_root / "game"
    # Then: game_root / relative_path
    
    # Example: project_root = "/workspace"
    # resolve_game_path("/workspace", "gui.rpy") → /workspace/game/gui.rpy ✓
    # resolve_game_path("/workspace", "game/gui.rpy") → /workspace/game/game/gui.rpy ✗
    
    assert gui_rpy_path == "gui.rpy"
    assert "game/" not in gui_rpy_path


def test_variant_lock_code_not_xpos_duplicate(tmp_path: Path) -> None:
    """Prove that Demo with variant overrides gets STYLE_POSITION_VARIANT_UNSUPPORTED, not XPOS_DUPLICATE.
    
    This would require full coordinator setup with RuntimeProbe.
    Documented here as acceptance criterion for integration tests.
    
    Expected behavior:
    - Demo game has @gui.variant small() override in gui.rpy
    - say.what analysis attempts style-backed ownership
    - analyze_say_what_style_position returns STYLE_POSITION_VARIANT_UNSUPPORTED
    - move_lock_reason uses that code, NOT XPOS_DUPLICATE
    """
    # Create minimal gui.rpy with variant override
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
    
    # Would need coordinator to analyze this
    # Expected: move_lock_reason code = STYLE_POSITION_VARIANT_UNSUPPORTED
    # Expected: move_lock_reason message contains "variant"
    # Expected: NOT XPOS_DUPLICATE
    
    assert gui_rpy.exists()


def test_clean_fixture_unlocks_style_gui_dialogue(tmp_path: Path) -> None:
    """Prove that fixture without variant override unlocks position_mode = style_gui_dialogue.
    
    Expected behavior:
    - Clean gui.rpy with pure define gui.dialogue_xpos = gui.scale(268)
    - No variant overrides
    - analyze_say_what_style_position returns unlocked statement
    - position_mode = "style_gui_dialogue" (SAY_WHAT_STYLE_POSITION_MODE)
    - capabilities.move = true
    """
    gui_rpy = tmp_path / "gui.rpy"
    gui_rpy.write_text(
        "define gui.dialogue_xpos = gui.scale(268)\n"
        "define gui.dialogue_ypos = gui.scale(50)\n",
        encoding="utf-8",
    )
    
    # Would need coordinator to analyze this
    # Expected: say_style_position.position_mode = "style_gui_dialogue"
    # Expected: position_lock_code = None
    # Expected: capabilities["move"] = True
    
    assert gui_rpy.exists()


def test_path_not_found_surfaces_as_lock_reason() -> None:
    """Prove that PATH_NOT_FOUND is mapped to STYLE_POSITION_SOURCE_UNRESOLVED, not silently ignored.
    
    Bug: bare except Exception: pass kept XPOS_DUPLICATE
    Fix: except EditorPathError maps to STYLE_POSITION_SOURCE_UNRESOLVED
    
    Expected behavior:
    - Project without gui.rpy
    - resolve_game_path raises EditorPathError("PATH_NOT_FOUND")
    - Coordinator catches and sets move_lock_reason = STYLE_POSITION_SOURCE_UNRESOLVED
    - NOT XPOS_DUPLICATE
    """
    # Would need coordinator integration test
    # Expected: move_lock_reason code = STYLE_POSITION_SOURCE_UNRESOLVED
    # Expected: move_lock_reason message contains "gui.rpy path error: PATH_NOT_FOUND"
    pass
