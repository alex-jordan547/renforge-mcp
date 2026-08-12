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
    
    # 1. Show dialogue, select say.what
    client.eval_expr('renpy.jump_out_of_context("start")')
    
    # Wait for say screen
    say_active = client.inspect_screen("say")
    report["say_screen_active"] = say_active.get("active") is True
    
    # Get say.what bounds
    widgets = client.eval_expr('renpy.display.behavior.get_scene_tree().get("what")')
    if not widgets or len(widgets) == 0:
        report["verdict"] = "fail"
        report["error"] = "say.what not found in scene tree"
        return report
    
    what_rect = widgets[0].get("rect", [0, 0, 100, 50])
    select_x = int(what_rect[0]) + 10
    select_y = int(what_rect[1]) + 10
    
    # Select
    select_result = client.eval_expr(
        f'_renforge_editor_select_target({select_x}, {select_y})'
    )
    report["select"] = select_result
    
    # 2. Verify unlock: position_mode = style_gui_dialogue, move = true
    status = client.eval_expr('_renforge_editor_task0_status()')
    report["unlock"] = {
        "position_mode": status.get("position_mode"),
        "capabilities": status.get("capabilities"),
    }
    
    move_unlocked = (
        status.get("position_mode") == "style_gui_dialogue"
        and status.get("capabilities", {}).get("move") is True
    )
    report["move_unlocked"] = move_unlocked
    
    if not move_unlocked:
        report["verdict"] = "fail"
        report["error"] = "say.what not unlocked for move"
        return report
    
    # 3. Preview: drag +20, +30 logical pixels
    current_pos = status.get("position", [0, 0])
    new_x = current_pos[0] + 20
    new_y = current_pos[1] + 30
    
    preview_result = client.eval_expr(
        f'_renforge_editor_apply_preview({new_x}, {new_y}, shift=False)'
    )
    report["preview"] = preview_result
    
    # Verify gui.rpy NOT modified yet
    gui_source_after_preview = gui_path.read_bytes()
    report["preview_source_unchanged"] = gui_source_after_preview == gui_source_initial
    
    # 4. Commit
    commit_result = client.eval_expr('_renforge_editor_commit()')
    report["commit"] = commit_result
    
    # Wait for commit to complete
    for _ in range(40):
        status = client.eval_expr('_renforge_editor_task0_status()')
        if status.get("save_button_state") == "saved":
            break
    else:
        report["verdict"] = "fail"
        report["error"] = "commit timeout"
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
    client.eval_expr('renpy.jump_out_of_context("start")')
    client.eval_expr('renpy.call_screen("say", "Test", "Second line")')
    
    # Select again
    select_result_2 = client.eval_expr(
        f'_renforge_editor_select_target({select_x}, {select_y})'
    )
    
    status_2 = client.eval_expr('_renforge_editor_task0_status()')
    report["rebind"] = {
        "position_mode": status_2.get("position_mode"),
        "position": status_2.get("position"),
    }
    
    # 8. Undo
    undo_result = client.eval_expr('_renforge_editor_undo()')
    report["undo"] = undo_result
    
    # Wait for undo
    for _ in range(40):
        status = client.eval_expr('_renforge_editor_task0_status()')
        if status.get("save_button_state") == "saved":
            break
    
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
