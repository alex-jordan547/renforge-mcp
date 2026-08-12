#!/usr/bin/env python3
"""Debug script to diagnose coordinator ownership check during live test."""

import sys
import time
sys.path.insert(0, '/workspace/src')

from pathlib import Path
from renforge.sdk import get_or_install_sdk
from renforge.project import RenpyProject
from renforge.bridge.launcher import launch_with_bridge
from renforge.editor.source import (
    analyze_say_dialogue_style_binding,
    analyze_say_what_style_position,
)

def diagnose_live_ownership(fixture_path: Path):
    """Launch Ren'Py, show say screen, select, then diagnose ownership locally."""
    
    sdk = get_or_install_sdk("8.5.3", project_root=fixture_path)
    
    print("=" * 70)
    print("DIAGNOSTIC: Live Ownership Check")
    print("=" * 70)
    
    with launch_with_bridge(
        sdk,
        RenpyProject(fixture_path),
        startup_timeout=120,
        editor=True,
    ) as session:
        client = session.client
        
        print("\n1. Show say screen...")
        client.eval_expr(
            'renpy.show_screen("say", who=None, what="Test dialogue", _layer="screens")'
        )
        
        print("\n2. Open editor...")
        for _ in range(40):
            if client.inspect_screen("_renforge_editor_launcher").get("active"):
                break
            time.sleep(0.2)
        
        client.click_element(text="RF", exact=True, screen="_renforge_editor_launcher")
        
        for _ in range(40):
            if client.inspect_screen("_renforge_editor_overlay").get("active"):
                break
            time.sleep(0.05)
        
        print("\n3. Select say.what...")
        tree = client.scene_tree(types=["text"], detail="semantic")
        nodes = tree.get("nodes", [])
        
        what_bounds = None
        for node in nodes:
            if isinstance(node, dict) and "Test dialogue" in str(node.get("text", "")):
                bounds = node.get("bounds")
                if bounds:
                    what_bounds = {"x": bounds["x"], "y": bounds["y"]}
                    break
        
        if not what_bounds:
            print("✗ Could not find say.what bounds")
            return
        
        select_result = client.eval_expr(
            f'_renforge_editor_select({what_bounds["x"] + 10}, {what_bounds["y"] + 10})'
        )
        print(f"   Select result: {select_result.get('ok')}")
        
        print("\n4. Wait for analysis...")
        for _ in range(100):
            status = client.request("editor_task0_status", {})
            if status.get("selected_lock_reason") != "ANALYZING":
                break
            time.sleep(0.1)
        
        print(f"   position_mode: {status.get('position_mode')}")
        print(f"   capabilities.move: {status.get('capabilities', {}).get('move')}")
        print(f"   selected_lock_reason: {status.get('selected_lock_reason')}")
        
        print("\n5. LOCAL ownership check (same fixture files)...")
        
        # Read from the LIVE fixture path (where Ren'Py is running)
        screens_path = fixture_path / "game" / "screens.rpy"
        gui_path = fixture_path / "game" / "gui.rpy"
        
        if not screens_path.exists():
            print(f"   ✗ screens.rpy not found at {screens_path}")
            return
        
        if not gui_path.exists():
            print(f"   ✗ gui.rpy not found at {gui_path}")
            return
        
        print(f"   Reading from: {fixture_path}")
        
        screens_source = screens_path.read_text()
        gui_source = gui_path.read_text()
        
        # Part 3: Style binding
        print("\n   Part 3: Style binding analysis...")
        style_binding = analyze_say_dialogue_style_binding(
            screens_source,
            xpos_var="gui.dialogue_xpos",
            ypos_var="gui.dialogue_ypos",
        )
        print(f"      binding_proven: {style_binding.binding_proven}")
        if not style_binding.binding_proven:
            print(f"      lock_code: {style_binding.lock_code}")
            print(f"      lock_message: {style_binding.lock_message}")
        
        # Part 4: GUI analysis
        print("\n   Part 4: GUI variable analysis...")
        gui_analysis = analyze_say_what_style_position(
            gui_source,
            xpos_var="gui.dialogue_xpos",
            ypos_var="gui.dialogue_ypos",
        )
        print(f"      xpos: {gui_analysis.xpos}")
        print(f"      ypos: {gui_analysis.ypos}")
        print(f"      position_mode: {gui_analysis.position_mode}")
        if gui_analysis.position_lock_code:
            print(f"      position_lock_code: {gui_analysis.position_lock_code}")
        
        print("\n" + "=" * 70)
        
        if (style_binding.binding_proven and 
            gui_analysis.xpos and 
            gui_analysis.ypos and 
            not gui_analysis.position_lock_code):
            print("✓ LOCAL ownership check: PASS")
            if status.get('position_mode') is None:
                print("✗ But coordinator set position_mode=None")
                print("\n** MISMATCH: Local analysis succeeds, coordinator fails **")
            else:
                print("✓ Coordinator also unlocked")
        else:
            print("✗ LOCAL ownership check: FAIL")
            if status.get('position_mode') is not None:
                print("✗ But coordinator unlocked anyway?")
            else:
                print("✓ Coordinator also locked (consistent)")

if __name__ == "__main__":
    fixture = Path("/tmp/say_what_live_diagnostic")
    
    # Copy fixture fresh
    import shutil
    src = Path("/workspace/tests/fixtures/say_what_clean")
    if fixture.exists():
        shutil.rmtree(fixture)
    shutil.copytree(src, fixture)
    
    # Inject editor
    from run_say_what_live_test import inject_renforge_editor
    inject_renforge_editor(fixture)
    
    diagnose_live_ownership(fixture)
