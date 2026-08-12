"""Opt-in live test harness for #81 say.what style position.

Gate with environment variable: RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1

Scenario:
1. Select say.what widget
2. Verify unlock (move: true, position_mode: style_gui_dialogue)
3. Preview drag (no TypeError, no dialogue advance)
4. Save (gui.rpy patched with delta, screens.rpy unchanged)
5. Reload + rebind (attestation)
6. Verify geometry ≤1px agreement
7. Show second dialogue line (global scope proof)
8. Undo (byte-identical restore + geometry agreement)
9. Redo (reapply + geometry agreement)

Fixture requirements:
- Clean gui.rpy WITHOUT @gui.variant xpos/ypos overrides
- Standard screens.rpy with screen say + text what id "what"
- Script.rpy with at least 2 dialogue lines

Blocked by:
- Requires Ren'Py 8.5.3 runtime environment
- Requires full coordinator + bridge + editor setup
- Requires fixture project without variant overrides (Demo has variants)

Status: Structure created, full implementation deferred pending runtime setup.
"""

from __future__ import annotations

import os

import pytest


def _is_live_enabled() -> bool:
    """Check if live testing is enabled via environment variable."""
    return os.environ.get("RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE", "0") == "1"


@pytest.mark.skipif(not _is_live_enabled(), reason="Live testing not enabled (set RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1)")
def test_say_what_style_position_live_select_and_unlock() -> None:
    """Live test: Select say.what and verify unlock."""
    # TODO: Requires Ren'Py 8.5.3 runtime + fixture project
    # Expected: capabilities["move"] = True
    # Expected: position_mode = "style_gui_dialogue"
    pytest.skip("Requires Ren'Py 8.5.3 runtime environment")


@pytest.mark.skipif(not _is_live_enabled(), reason="Live testing not enabled")
def test_say_what_style_position_live_preview_without_error() -> None:
    """Live test: Preview drag without TypeError or dialogue advance."""
    # TODO: Requires Ren'Py 8.5.3 runtime + fixture project
    # Expected: Preview mutates style.say_dialogue.xpos/ypos
    # Expected: No TypeError: missing a required argument: 'who'
    # Expected: Dialogue text stays visible, no advance
    pytest.skip("Requires Ren'Py 8.5.3 runtime environment")


@pytest.mark.skipif(not _is_live_enabled(), reason="Live testing not enabled")
def test_say_what_style_position_live_save_and_reload() -> None:
    """Live test: Save patches gui.rpy with delta, reload succeeds."""
    # TODO: Requires Ren'Py 8.5.3 runtime + fixture project
    # Expected: gui.rpy patched with authored + delta (window-relative)
    # Expected: screens.rpy unchanged (identity-only path)
    # Expected: Reload succeeds, attestation passes
    # Expected: Geometry agreement ≤1px
    pytest.skip("Requires Ren'Py 8.5.3 runtime environment")


@pytest.mark.skipif(not _is_live_enabled(), reason="Live testing not enabled")
def test_say_what_style_position_live_global_scope() -> None:
    """Live test: Second dialogue line appears at new position (global scope proof)."""
    # TODO: Requires Ren'Py 8.5.3 runtime + fixture project
    # Expected: Advance to second dialogue line
    # Expected: Second line also at new position (global scope)
    pytest.skip("Requires Ren'Py 8.5.3 runtime environment")


@pytest.mark.skipif(not _is_live_enabled(), reason="Live testing not enabled")
def test_say_what_style_position_live_undo_redo() -> None:
    """Live test: Undo is byte-identical, redo reapplies."""
    # TODO: Requires Ren'Py 8.5.3 runtime + fixture project
    # Expected: Undo restores original gui.rpy bytes exactly
    # Expected: Geometry returns to original ≤1px
    # Expected: Redo reapplies delta to gui.rpy
    # Expected: Geometry returns to edited position ≤1px
    pytest.skip("Requires Ren'Py 8.5.3 runtime environment")


@pytest.mark.skipif(not _is_live_enabled(), reason="Live testing not enabled")
def test_say_what_style_position_live_variant_fixture_locks() -> None:
    """Live test: Fixture WITH variant override stays locked."""
    # TODO: Requires Ren'Py 8.5.3 runtime + variant fixture
    # Expected: Demo or fixture with @gui.variant small() xpos writer
    # Expected: capabilities["move"] = False
    # Expected: lock_code = STYLE_POSITION_VARIANT_UNSUPPORTED
    # Expected: NOT XPOS_DUPLICATE
    pytest.skip("Requires Ren'Py 8.5.3 runtime environment")


# Run with: RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 pytest tests/test_editor_say_what_live.py -v
