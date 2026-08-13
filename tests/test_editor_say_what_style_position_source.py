"""Deterministic regression tests for issue #81 say.what style position contract."""

from __future__ import annotations

import pytest

from renforge.editor.source import (
    SAY_WHAT_STYLE_POSITION_MODE,
    EditorSourceError,
    analyze_say_what_style_position,
    apply_say_what_style_position_patch,
)


def test_analyze_say_what_style_position_unlocks_pure_gui_scale() -> None:
    """Test that a supported gui.scale() form unlocks."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode == "style_gui_dialogue"
    assert parsed.xpos == 268
    assert parsed.ypos == 50
    assert parsed.position_lock_code is None
    assert parsed.xpos_span is not None
    assert parsed.ypos_span is not None


def test_apply_say_what_style_position_patch_preserves_unrelated_bytes() -> None:
    """Test that only the gui.scale() integers are rewritten."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)
define gui.dialogue_width = gui.scale(744)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    patched = apply_say_what_style_position_patch(
        gui_file.encode("utf-8"),
        parsed,
        x=300,
        y=100,
    ).decode("utf-8")
    expected = """\
define gui.dialogue_xpos = gui.scale(300)
define gui.dialogue_ypos = gui.scale(100)
define gui.dialogue_width = gui.scale(744)
"""
    assert patched == expected
    # Outside the modified integers: byte-identical.
    before_norm = gui_file.replace("268", "XXX").replace("50", "YYY")
    after_norm = patched.replace("300", "XXX").replace("100", "YYY")
    assert before_norm == after_norm


def test_apply_say_what_style_position_patch_preserves_gui_scale_wrapper() -> None:
    """Test that the gui.scale(...) wrapper is preserved."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)  # comment
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    patched = apply_say_what_style_position_patch(
        gui_file.encode("utf-8"),
        parsed,
        x=300,
        y=100,
    ).decode("utf-8")
    assert "gui.scale(300)" in patched
    assert "gui.scale(100)" in patched
    assert "# comment" in patched


def test_analyze_say_what_style_position_rejects_missing_xpos() -> None:
    """Test that missing xpos variable locks with stable code."""
    gui_file = "define gui.dialogue_ypos = gui.scale(50)\n"
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"


def test_analyze_say_what_style_position_rejects_missing_ypos() -> None:
    """Test that missing ypos variable locks with stable code."""
    gui_file = "define gui.dialogue_xpos = gui.scale(268)\n"
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"


def test_analyze_say_what_style_position_rejects_duplicate_xpos() -> None:
    """Test that duplicate xpos definitions lock with ambiguous code."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_xpos = gui.scale(300)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_SOURCE_AMBIGUOUS"


def test_analyze_say_what_style_position_rejects_expression() -> None:
    """Test that expression forms remain locked."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268) if flag else gui.scale(100)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_EXPRESSION_UNSUPPORTED"


def test_analyze_say_what_style_position_rejects_non_gui_scale() -> None:
    """Test that non-gui.scale() forms remain locked."""
    gui_file = """\
define gui.dialogue_xpos = 268
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_EXPRESSION_UNSUPPORTED"


def test_analyze_say_what_style_position_rejects_arithmetic() -> None:
    """Test that arithmetic expressions remain locked."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268 + 10)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_EXPRESSION_UNSUPPORTED"


def test_analyze_say_what_style_position_rejects_variant_override() -> None:
    """Test that phone/small variant overrides remain locked (issue #81 critical finding #3)."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)

init python:
    @gui.variant
    def small():
        gui.dialogue_xpos = gui.scale(90)
        gui.dialogue_width = gui.scale(1100)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode is None
    assert parsed.position_lock_code == "STYLE_POSITION_VARIANT_UNSUPPORTED"


def test_analyze_say_what_style_position_ignores_variant_comments() -> None:
    """Test that comments inside variant don't trigger false positive (polish #2)."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)

init python:
    @gui.variant
    def small():
        # Note: gui.dialogue_xpos = gui.scale(100) would override for phones
        # Similarly, gui.dialogue_ypos = gui.scale(20) would set vertical position
        pass
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    # Should unlock: comments don't count as variant writers
    assert parsed.xpos == 268
    assert parsed.ypos == 50
    assert parsed.position_mode == SAY_WHAT_STYLE_POSITION_MODE
    assert parsed.position_lock_code is None


def test_apply_say_what_style_position_patch_refuses_locked_statement() -> None:
    """Test that locked statements refuse patching."""
    gui_file = "define gui.dialogue_ypos = gui.scale(50)\n"
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"
    with pytest.raises(EditorSourceError) as exc:
        apply_say_what_style_position_patch(
            gui_file.encode("utf-8"),
            parsed,
            x=300,
            y=100,
        )
    assert exc.value.code == "STYLE_POSITION_SOURCE_UNRESOLVED"


def test_apply_say_what_style_position_patch_refuses_stale_source() -> None:
    """Test that stale source is rejected before publication."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    stale = gui_file.replace("268", "999")
    with pytest.raises(EditorSourceError) as exc:
        apply_say_what_style_position_patch(
            stale.encode("utf-8"),
            parsed,
            x=300,
            y=100,
        )
    assert exc.value.code == "STALE_SOURCE"


def test_analyze_say_what_style_position_preserves_whitespace_and_comments() -> None:
    """Test that whitespace, comments, and unrelated lines are preserved."""
    gui_file = """\
# Dialogue positioning
define gui.dialogue_xpos = gui.scale(268)  # horizontal offset
define gui.dialogue_ypos = gui.scale(50)   # vertical offset

# Other settings
define gui.dialogue_width = gui.scale(744)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    patched = apply_say_what_style_position_patch(
        gui_file.encode("utf-8"),
        parsed,
        x=300,
        y=100,
    ).decode("utf-8")
    assert "# Dialogue positioning" in patched
    assert "# horizontal offset" in patched
    assert "# vertical offset" in patched
    assert "# Other settings" in patched
    assert "gui.dialogue_width = gui.scale(744)" in patched


def test_analyze_say_what_style_position_accepts_tabs_and_spaces() -> None:
    """Test that mixed indentation is supported."""
    gui_file = """\
\tdefine gui.dialogue_xpos = gui.scale(268)
    define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode == "style_gui_dialogue"
    patched = apply_say_what_style_position_patch(
        gui_file.encode("utf-8"),
        parsed,
        x=300,
        y=100,
    ).decode("utf-8")
    assert "\tdefine gui.dialogue_xpos = gui.scale(300)" in patched
    assert "    define gui.dialogue_ypos = gui.scale(100)" in patched


def test_apply_say_what_style_position_patch_supports_unicode() -> None:
    """Test that UTF-8 source is preserved."""
    gui_file = """\
# Position du dialogue — français
define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    patched = apply_say_what_style_position_patch(
        gui_file.encode("utf-8"),
        parsed,
        x=300,
        y=100,
    ).decode("utf-8")
    assert "# Position du dialogue — français" in patched
    assert "gui.scale(300)" in patched
    assert "gui.scale(100)" in patched


def test_analyze_say_what_style_position_supports_negative_values() -> None:
    """Test that negative gui.scale() arguments are supported."""
    gui_file = """\
define gui.dialogue_xpos = gui.scale(-10)
define gui.dialogue_ypos = gui.scale(50)
"""
    parsed = analyze_say_what_style_position(
        gui_file,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    assert parsed.position_mode == "style_gui_dialogue"
    assert parsed.xpos == -10
    patched = apply_say_what_style_position_patch(
        gui_file.encode("utf-8"),
        parsed,
        x=-20,
        y=100,
    ).decode("utf-8")
    assert "gui.scale(-20)" in patched
