"""Live test runner for #81 say.what style position.

Patterned on editor_style_color_runner but for dialogue position backed by gui.rpy.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from renforge.bridge.client import BridgeClient


FIXTURE_SCREEN = "say"
TARGET_ID = "what"


def run_editor_say_what_style_position_live_scenario(
    client: BridgeClient,
    *,
    fixture_path: Path,
    gui_path: Path,
) -> dict[str, Any]:
    """Run full #81 live scenario: select, unlock, preview, save, reload, undo.
    
    Args:
        client: Ren'Py bridge client
        fixture_path: Path to screens.rpy
        gui_path: Path to gui.rpy (write target)
        
    Returns:
        Detailed report dict with pass/fail verdict.
    """
    report: dict[str, Any] = {}
    
    # Read initial source
    gui_source_initial = gui_path.read_bytes()
    screens_source_initial = fixture_path.read_bytes()
    
    # 1. Verify say screen is active (shown by harness before editor opened)
    say_active = client.inspect_screen("say")
    report["say_screen_active"] = say_active.get("active") is True
    if not report["say_screen_active"]:
        report["verdict"] = "fail"
        report["error"] = "say screen not active"
        return report
    
    # Get say.what bounds using scene_tree
    tree = client.scene_tree(types=["text"], detail="semantic")
    nodes = tree.get("nodes") if isinstance(tree, dict) else []
    
    # Find the "what" widget by looking for the text widget inside "say" screen
    what_bounds = None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        # Look for the node that has our test text
        if "Test dialogue for RenForge #81" in str(node.get("text") or ""):
            bounds = node.get("bounds")
            if isinstance(bounds, dict):
                what_bounds = {
                    "x": int(bounds["x"]),
                    "y": int(bounds["y"]),
                    "width": int(bounds["width"]),
                    "height": int(bounds["height"]),
                }
                break
    
    report["what_bounds"] = what_bounds
    if not what_bounds:
        report["verdict"] = "fail"
        report["error"] = "say.what not found in scene tree"
        return report
    
    select_x = what_bounds["x"] + 10
    select_y = what_bounds["y"] + 10
    
    # Select
    select_result = client.eval_expr(
        f'_renforge_editor_select({select_x}, {select_y})'
    )
    report["select"] = select_result
    
    # 2. Wait for analysis to complete, then verify unlock
    import time
    for _ in range(100):  # Wait up to 10 seconds
        status = client.request("editor_task0_status", {})
        lock_reason = status.get("selected_lock_reason")
        if lock_reason != "ANALYZING":
            break
        time.sleep(0.1)
    else:
        report["verdict"] = "fail"
        report["error"] = "analysis timeout (still ANALYZING after 10s)"
        return report
    
    report["unlock"] = {
        "position_mode": status.get("position_mode"),
        "capabilities": status.get("capabilities"),
        "selected_lock_reason": status.get("selected_lock_reason"),
        "save_enabled": status.get("save_enabled"),
    }
    
    # Check if move is unlocked
    capabilities = status.get("capabilities") or {}
    move_unlocked = capabilities.get("move") is True
    report["move_unlocked"] = move_unlocked
    
    if not move_unlocked:
        report["verdict"] = "fail"
        report["error"] = "say.what not unlocked for move"
        return report
    
    # 3. Preview: drag +20, +30 logical pixels
    current_pos = status.get("position") or [268, 50]  # Fallback to authored pos
    new_x = current_pos[0] + 20
    new_y = current_pos[1] + 30
    
    preview_result = client.eval_expr(
        f'_renforge_editor_apply_preview({new_x}, {new_y}, shift=False)'
    )
    report["preview"] = preview_result
    
    # Verify gui.rpy NOT modified yet
    gui_source_after_preview = gui_path.read_bytes()
    report["preview_source_unchanged"] = gui_source_after_preview == gui_source_initial
    
    # 4. Save (click save button, not direct commit call)
    save_click = client.click_element(id="rf_save", screen="_renforge_editor_overlay")
    report["save_click"] = save_click
    
    # Wait for save/commit to complete
    import time
    last_status = None
    for i in range(80):
        status = client.request("editor_task0_status", {})
        last_status = status
        status_code = status.get("status_code")
        
        # Log progress every 10 iterations
        if i % 10 == 0:
            print(f"   Waiting for commit... status_code={status_code}, save_in_progress={status.get('save_in_progress')}")
        
        if status_code == "reload_committed":
            print(f"   ✓ Commit completed (after {i * 0.5:.1f}s)")
            break
        elif status_code in ("reload_failed", "commit_failed"):
            report["verdict"] = "fail"
            report["error"] = f"commit failed: {status_code}"
            report["save_error"] = status.get("save_error")
            report["last_status"] = status
            return report
        
        time.sleep(0.5)
    else:
        report["verdict"] = "fail"
        report["error"] = "commit timeout"
        report["last_status_code"] = last_status.get("status_code") if last_status else None
        report["save_in_progress"] = last_status.get("save_in_progress") if last_status else None
        report["save_error"] = last_status.get("save_error") if last_status else None
        return report
    
    # 5. Verify gui.rpy patched with delta, NOT absolute screen coords
    gui_source_after_commit = gui_path.read_bytes()
    report["gui_source_changed"] = gui_source_after_commit != gui_source_initial
    
    # Parse patched values
    gui_text = gui_source_after_commit.decode("utf-8")
    xpos_patched = None
    ypos_patched = None
    for line in gui_text.splitlines():
        if "gui.dialogue_xpos" in line and "=" in line:
            xpos_patched = int(line.split("(")[1].split(")")[0])
        if "gui.dialogue_ypos" in line and "=" in line:
            ypos_patched = int(line.split("(")[1].split(")")[0])
    
    report["patched_xpos"] = xpos_patched
    report["patched_ypos"] = ypos_patched
    
    # Expected: authored + delta (268+20=288, 50+30=80)
    report["delta_correct"] = xpos_patched == 288 and ypos_patched == 80
    
    # 6. Reload
    client.eval_expr('renpy.utter_restart()')
    
    # Wait for reload
    for _ in range(40):
        if client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
    
    # 7. Rebind to second dialogue line, verify global scope
    client.eval_expr('renpy.show_screen("say", who="Test", what="Second line", _layer="screens")')
    
    # Select again
    select_result_2 = client.eval_expr(
        f'_renforge_editor_select({select_x}, {select_y})'
    )
    
    status_2 = client.request("editor_task0_status", {})
    report["rebind"] = {
        "position_mode": status_2.get("position_mode"),
        "position": status_2.get("position"),
    }
    
    # 8. Undo (click undo button)
    undo_click = client.click_element(id="rf_undo", screen="_renforge_editor_overlay")
    report["undo_click"] = undo_click
    
    # Wait for undo
    for _ in range(80):
        status = client.request("editor_task0_status", {})
        if status.get("status_code") == "reload_committed":
            break
        time.sleep(0.5)
    
    # 9. Verify byte-identical undo
    gui_source_after_undo = gui_path.read_bytes()
    report["undo_byte_identical"] = gui_source_after_undo == gui_source_initial
    
    # 10. Verdict
    report["verdict"] = (
        "pass"
        if move_unlocked
        and report.get("delta_correct")
        and report.get("undo_byte_identical")
        else "fail"
    )
    
    return report
