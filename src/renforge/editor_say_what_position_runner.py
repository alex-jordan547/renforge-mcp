"""Live test runner for #81 say.what style position.

Patterned on editor_style_color_runner but for dialogue position backed by gui.rpy.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from renforge.bridge.client import BridgeClient, BridgeProtocolError


FIXTURE_SCREEN = "say"
TARGET_ID = "what"


def _text_bounds(client: BridgeClient, text: str) -> dict[str, int] | None:
    tree = client.scene_tree(types=["text"], detail="semantic")
    nodes = tree.get("nodes") if isinstance(tree, dict) else []
    for node in nodes:
        if not isinstance(node, dict) or text not in str(node.get("text") or ""):
            continue
        bounds = node.get("bounds")
        if isinstance(bounds, dict):
            return {
                "x": int(bounds["x"]),
                "y": int(bounds["y"]),
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
            }
    return None


def _wait_for_transaction(
    client: BridgeClient,
    source_path: Path,
    expected_bytes: bytes,
    *,
    timeout: float = 60.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        try:
            status = client.request("editor_task0_status", {})
            last_status = status
            if (
                status.get("status_code") == "reload_committed"
                and status.get("save_in_progress") is False
                and source_path.read_bytes() == expected_bytes
            ):
                return status
        except (BridgeProtocolError, OSError):
            pass
        time.sleep(0.25)
    return last_status


def _within_one_pixel(bounds: dict[str, int] | None, expected: list[int]) -> bool:
    return bool(
        bounds
        and abs(bounds["x"] - expected[0]) <= 1
        and abs(bounds["y"] - expected[1]) <= 1
    )


def run_editor_say_what_style_position_live_scenario(
    client: BridgeClient,
    *,
    fixture_path: Path,
    gui_path: Path,
    screenshot_path: Path | None = None,
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
    # FIRST: Clean any stale transactions from previous runs
    transaction_root = fixture_path.parent.parent / ".renforge" / "editor-transactions"
    if transaction_root.exists():
        import shutil
        try:
            shutil.rmtree(transaction_root)
            print(f"   Cleaned stale transactions from {transaction_root}")
        except OSError:
            pass

    say_active = client.inspect_screen("say")
    say_scope_before = {
        key: (say_active.get("scope") or {}).get(key)
        for key in ("who", "what")
    }
    report["say_screen_active"] = say_active.get("active") is True
    if not report["say_screen_active"]:
        report["verdict"] = "fail"
        report["error"] = "say screen not active"
        return report

    what_bounds = _text_bounds(client, "Test dialogue for RenForge #81")

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

    # 3. Preview: the editor API takes screen coordinates. The bridge maps the
    # screen-space delta back to the window-relative gui.rpy values.
    new_x = what_bounds["x"] + 20
    new_y = what_bounds["y"] + 30
    expected_screen_position = [new_x, new_y]

    preview_result = client.eval_expr(
        f'_renforge_editor_apply_preview({new_x}, {new_y}, shift=False)'
    )
    report["preview"] = preview_result

    preview_bounds = None
    for _ in range(30):
        preview_bounds = _text_bounds(client, "Test dialogue for RenForge #81")
        if _within_one_pixel(preview_bounds, expected_screen_position):
            break
        time.sleep(0.1)
    say_after_preview = client.inspect_screen("say")
    say_scope_after = {
        key: (say_after_preview.get("scope") or {}).get(key)
        for key in ("who", "what")
    }
    report["preview_bounds"] = preview_bounds
    report["preview_geometry_correct"] = _within_one_pixel(
        preview_bounds, expected_screen_position
    )
    report["preview_preserved_dialogue"] = bool(
        say_after_preview.get("active") is True
        and say_scope_after == say_scope_before
    )

    # Verify gui.rpy NOT modified yet
    gui_source_after_preview = gui_path.read_bytes()
    report["preview_source_unchanged"] = gui_source_after_preview == gui_source_initial

    # 4. Save (click save button, not direct commit call)
    save_click = client.click_element(id="rf_save", screen="_renforge_editor_overlay")
    report["save_click"] = save_click

    expected_committed = gui_source_initial.replace(
        b"gui.dialogue_xpos = gui.scale(268)",
        b"gui.dialogue_xpos = gui.scale(288)",
    ).replace(
        b"gui.dialogue_ypos = gui.scale(50)",
        b"gui.dialogue_ypos = gui.scale(80)",
    )
    last_status = _wait_for_transaction(client, gui_path, expected_committed)
    if not last_status or last_status.get("status_code") != "reload_committed":
        report["verdict"] = "fail"
        report["error"] = "commit timeout"
        report["last_status"] = last_status
        try:
            report["reload_slot"] = client.eval_expr(
                'renpy.session.get("_reload_slot")'
            )
            report["attestation_screen_handoff"] = client.eval_expr(
                '(_renforge_editor_state().pending_attestation_screen, '
                '_renforge_editor_state().pending_attestation_screen_kwargs)'
            )
        except BridgeProtocolError:
            report["reload_slot"] = "bridge unavailable"
        try:
            report["say_after_reload"] = client.inspect_screen("say")
            report["scene_texts_after_reload"] = [
                node.get("text")
                for node in (client.scene_tree(types=["text"], detail="semantic").get("nodes") or [])
                if isinstance(node, dict) and node.get("text")
            ]
        except BridgeProtocolError:
            report["say_after_reload"] = "bridge unavailable"
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
    report["only_gui_position_changed"] = gui_source_after_commit == expected_committed
    report["screens_source_unchanged"] = fixture_path.read_bytes() == screens_source_initial

    # 6. The commit handshake already reloaded the script. Rebind to a second
    # dialogue line and prove the global style lands at the dragged screen point.
    client.eval_expr('renpy.show_screen("say", who="Test", what="Second line", _layer="screens")')
    time.sleep(0.25)
    second_bounds = _text_bounds(client, "Second line")
    report["second_line_bounds"] = second_bounds
    report["committed_geometry_correct"] = _within_one_pixel(
        second_bounds, expected_screen_position
    )
    second_select = client.eval_expr(
        f'_renforge_editor_select({second_bounds["x"] + 10}, {second_bounds["y"] + 10})'
    ) if second_bounds else None
    report["second_line_select"] = second_select
    for _ in range(100):
        if client.request("editor_task0_status", {}).get("selected_lock_reason") != "ANALYZING":
            break
        time.sleep(0.1)
    if screenshot_path is not None and second_bounds:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot = client.screenshot()
        screenshot_path.write_bytes(screenshot)
        report["screenshot_path"] = str(screenshot_path)
        report["screenshot_sha256"] = hashlib.sha256(screenshot).hexdigest()

    # 7. Undo and require both the terminal handshake state and exact bytes.
    undo_click = client.click_element(id="rf_undo", screen="_renforge_editor_overlay")
    report["undo_click"] = undo_click
    undo_status = _wait_for_transaction(client, gui_path, gui_source_initial)
    report["undo_status_code"] = undo_status.get("status_code") if undo_status else None
    gui_source_after_undo = gui_path.read_bytes()
    report["undo_byte_identical"] = gui_source_after_undo == gui_source_initial
    client.eval_expr('renpy.show_screen("say", who="Test", what="Undo line", _layer="screens")')
    time.sleep(0.25)
    undo_bounds = _text_bounds(client, "Undo line")
    report["undo_geometry_restored"] = _within_one_pixel(
        undo_bounds, [what_bounds["x"], what_bounds["y"]]
    )

    # 8. Redo reverses the committed undo transaction, then must reproduce
    # both the exact patched bytes and the same runtime geometry.
    redo_click = client.click_element(id="rf_redo", screen="_renforge_editor_overlay")
    report["redo_click"] = redo_click
    redo_status = _wait_for_transaction(client, gui_path, gui_source_after_commit)
    report["redo_status_code"] = redo_status.get("status_code") if redo_status else None
    report["redo_byte_identical"] = gui_path.read_bytes() == gui_source_after_commit
    client.eval_expr('renpy.show_screen("say", who="Test", what="Redo line", _layer="screens")')
    time.sleep(0.25)
    redo_bounds = _text_bounds(client, "Redo line")
    report["redo_geometry_correct"] = _within_one_pixel(
        redo_bounds, expected_screen_position
    )

    report["verdict"] = (
        "pass"
        if move_unlocked
        and report.get("preview_geometry_correct")
        and report.get("preview_preserved_dialogue")
        and report.get("preview_source_unchanged")
        and report.get("delta_correct")
        and report.get("only_gui_position_changed")
        and report.get("screens_source_unchanged")
        and report.get("committed_geometry_correct")
        and report.get("undo_byte_identical")
        and report.get("undo_geometry_restored")
        and report.get("redo_byte_identical")
        and report.get("redo_geometry_correct")
        else "fail"
    )

    return report
