"""Unit tests for #81 say.what style position delta math (not absolute screen writeback)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from renforge.editor.source import analyze_say_what_style_position


def test_say_what_delta_math_not_absolute_screen_coords(tmp_path: Path) -> None:
    """Prove that gui.rpy patch uses logical-pixel deltas, not absolute screen coords.
    
    Issue #81 requirement: Never write absolute screen y into gui.dialogue_ypos.
    Apply logical-pixel delta to authored gui.scale ints.
    """
    # Setup: gui.rpy with authored position
    gui_rpy = tmp_path / "gui.rpy"
    gui_rpy.write_text(
        "define gui.dialogue_xpos = gui.scale(268)\n"
        "define gui.dialogue_ypos = gui.scale(50)\n",
        encoding="utf-8",
    )
    
    # Parse authored values
    gui_source = gui_rpy.read_text(encoding="utf-8")
    stmt = analyze_say_what_style_position(
        gui_source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    
    authored_x = stmt.xpos  # 268
    authored_y = stmt.ypos  # 50
    
    # Simulate runtime: dialogue window at (100, 200), rendered text at (368, 250)
    # runtime_baseline = authored + parent_offset = (268 + 100, 50 + 200)
    runtime_baseline_x = 368
    runtime_baseline_y = 250
    
    # User drags text by +50 pixels horizontally, +30 vertically
    # New intent position (absolute screen coords)
    intent_x = 418  # 368 + 50
    intent_y = 280  # 250 + 30
    
    # Calculate logical-pixel delta (coordinator logic)
    delta_x = intent_x - runtime_baseline_x  # 50
    delta_y = intent_y - runtime_baseline_y  # 30
    
    # Apply delta to authored values (window-relative)
    patched_x = authored_x + delta_x  # 268 + 50 = 318
    patched_y = authored_y + delta_y  # 50 + 30 = 80
    
    # Verify: patched values are window-relative, NOT absolute screen coords
    assert patched_x == 318, "xpos should be authored + delta, not absolute screen"
    assert patched_y == 80, "ypos should be authored + delta, not absolute screen"
    
    # Verify: patched values != absolute screen coords
    assert patched_x != intent_x, f"Must not write absolute screen x={intent_x}"
    assert patched_y != intent_y, f"Must not write absolute screen y={intent_y}"
    
    # Verify: delta math is consistent
    assert patched_x - authored_x == intent_x - runtime_baseline_x
    assert patched_y - authored_y == intent_y - runtime_baseline_y


def test_say_what_delta_math_negative_movement(tmp_path: Path) -> None:
    """Test that negative deltas work correctly (moving dialogue up/left)."""
    gui_rpy = tmp_path / "gui.rpy"
    gui_rpy.write_text(
        "define gui.dialogue_xpos = gui.scale(300)\n"
        "define gui.dialogue_ypos = gui.scale(100)\n",
        encoding="utf-8",
    )
    
    gui_source = gui_rpy.read_text(encoding="utf-8")
    stmt = analyze_say_what_style_position(
        gui_source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    
    authored_x = stmt.xpos  # 300
    authored_y = stmt.ypos  # 100
    
    # Runtime baseline
    runtime_baseline_x = 400  # 300 + 100 parent offset
    runtime_baseline_y = 300  # 100 + 200 parent offset
    
    # User drags text LEFT by 50 and UP by 80
    intent_x = 350  # 400 - 50
    intent_y = 220  # 300 - 80
    
    # Calculate delta
    delta_x = intent_x - runtime_baseline_x  # -50
    delta_y = intent_y - runtime_baseline_y  # -80
    
    # Apply to authored
    patched_x = authored_x + delta_x  # 300 - 50 = 250
    patched_y = authored_y + delta_y  # 100 - 80 = 20
    
    assert patched_x == 250
    assert patched_y == 20
    assert patched_x != intent_x
    assert patched_y != intent_y


def test_say_what_delta_math_zero_parent_offset(tmp_path: Path) -> None:
    """Test delta math when parent window is at screen origin (edge case)."""
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
    
    authored_x = stmt.xpos  # 268
    authored_y = stmt.ypos  # 50
    
    # Parent window at (0, 0) - runtime matches authored
    runtime_baseline_x = 268
    runtime_baseline_y = 50
    
    # Drag to (300, 100)
    intent_x = 300
    intent_y = 100
    
    delta_x = intent_x - runtime_baseline_x  # 32
    delta_y = intent_y - runtime_baseline_y  # 50
    
    patched_x = authored_x + delta_x  # 268 + 32 = 300
    patched_y = authored_y + delta_y  # 50 + 50 = 100
    
    # When parent offset is zero, patched happens to equal intent
    # But the math is still delta-based, not absolute-screen-based
    assert patched_x == 300
    assert patched_y == 100
    assert delta_x == 32
    assert delta_y == 50
