#!/usr/bin/env python3
"""Debug ownership proof for say_what_clean fixture."""

import sys
sys.path.insert(0, '/workspace/src')

from pathlib import Path
from renforge.editor.source import (
    analyze_say_dialogue_style_binding,
    analyze_say_what_style_position,
)

def main():
    fixture = Path("/workspace/tests/fixtures/say_what_clean/game")
    
    screens_rpy = (fixture / "screens.rpy").read_text()
    gui_rpy = (fixture / "gui.rpy").read_text()
    
    print("=" * 60)
    print("Testing ownership proof for say_what_clean fixture")
    print("=" * 60)
    
    # Part 1: Check style binding in screens.rpy
    print("\n1. Style binding analysis (screens.rpy)...")
    style_binding = analyze_say_dialogue_style_binding(
        screens_rpy,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    print(f"   binding_proven: {style_binding.binding_proven}")
    if not style_binding.binding_proven:
        print(f"   lock_code: {style_binding.lock_code}")
        print(f"   lock_message: {style_binding.lock_message}")
    
    # Part 2: Check gui.rpy analysis
    print("\n2. GUI variable analysis (gui.rpy)...")
    gui_analysis = analyze_say_what_style_position(
        gui_rpy,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    print(f"   xpos: {gui_analysis.xpos}")
    print(f"   ypos: {gui_analysis.ypos}")
    print(f"   xpos_span: {gui_analysis.xpos_span}")
    print(f"   ypos_span: {gui_analysis.ypos_span}")
    print(f"   position_mode: {gui_analysis.position_mode}")
    if gui_analysis.position_lock_code:
        print(f"   position_lock_code: {gui_analysis.position_lock_code}")
        print(f"   position_lock_message: {gui_analysis.position_lock_message}")
    
    print("\n" + "=" * 60)
    if style_binding.binding_proven and gui_analysis.xpos and gui_analysis.ypos and not gui_analysis.position_lock_code:
        print("✓ OWNERSHIP PROOF COMPLETE - should unlock")
        return 0
    else:
        print("✗ OWNERSHIP PROOF FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
