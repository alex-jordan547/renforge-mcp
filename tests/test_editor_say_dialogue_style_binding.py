"""Unit tests for say_dialogue style binding analysis (ownership proof for #81)."""

from __future__ import annotations

import pytest

from renforge.editor.source import (
    EditorSourceError,
    SayDialogueStyleBinding,
    analyze_say_dialogue_style_binding,
    prove_say_what_text_binding,
)


def test_analyze_say_dialogue_style_binding_unlocks_when_proven() -> None:
    """Prove unlock when style say_dialogue uniquely binds xpos/ypos to gui vars."""
    source = """
screen say(who, what):
    window:
        id "window"
        text what id "what"

style say_dialogue:
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is True
    assert binding.lock_code is None


def test_analyze_say_dialogue_style_binding_locks_when_style_missing() -> None:
    """Lock UNRESOLVED when style say_dialogue not found."""
    source = """
screen say(who, what):
    window:
        id "window"
        text what id "what"
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"
    assert "not found" in binding.lock_message.lower()


def test_analyze_say_dialogue_style_binding_locks_when_duplicate_styles() -> None:
    """Lock AMBIGUOUS when multiple style say_dialogue blocks found."""
    source = """
style say_dialogue:
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos

style say_dialogue:
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_SOURCE_AMBIGUOUS"
    assert "multiple" in binding.lock_message.lower()


def test_analyze_say_dialogue_style_binding_locks_when_wrong_xpos_var() -> None:
    """Lock UNRESOLVED when xpos references wrong variable."""
    source = """
style say_dialogue:
    xpos gui.other_xpos
    ypos gui.dialogue_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"
    assert "xpos" in binding.lock_message.lower()


def test_analyze_say_dialogue_style_binding_locks_when_wrong_ypos_var() -> None:
    """Lock UNRESOLVED when ypos references wrong variable."""
    source = """
style say_dialogue:
    xpos gui.dialogue_xpos
    ypos gui.other_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"
    assert "ypos" in binding.lock_message.lower()


def test_analyze_say_dialogue_style_binding_locks_when_xpos_expression() -> None:
    """Lock EXPRESSION_UNSUPPORTED when xpos uses arithmetic."""
    source = """
style say_dialogue:
    xpos gui.dialogue_xpos + 10
    ypos gui.dialogue_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_EXPRESSION_UNSUPPORTED"
    assert "xpos" in binding.lock_message.lower()


def test_analyze_say_dialogue_style_binding_locks_when_ypos_expression() -> None:
    """Lock EXPRESSION_UNSUPPORTED when ypos uses arithmetic."""
    source = """
style say_dialogue:
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos - 20
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_EXPRESSION_UNSUPPORTED"
    assert "ypos" in binding.lock_message.lower()


def test_analyze_say_dialogue_style_binding_locks_when_xpos_missing() -> None:
    """Lock UNRESOLVED when style has ypos but no xpos."""
    source = """
style say_dialogue:
    ypos gui.dialogue_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"


def test_analyze_say_dialogue_style_binding_locks_when_ypos_missing() -> None:
    """Lock UNRESOLVED when style has xpos but no ypos."""
    source = """
style say_dialogue:
    xpos gui.dialogue_xpos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False
    assert binding.lock_code == "STYLE_POSITION_SOURCE_UNRESOLVED"


def test_analyze_say_dialogue_style_binding_ignores_comments() -> None:
    """Prove comments do not interfere with binding analysis."""
    source = """
# This is a comment about style say_dialogue

style say_dialogue:
    # xpos gui.other_xpos  # commented out
    xpos gui.dialogue_xpos  # actual binding
    ypos gui.dialogue_ypos  # another comment
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is True


def test_analyze_say_dialogue_style_binding_handles_equals_syntax() -> None:
    """Prove unlock works with xpos=var syntax (no space)."""
    source = """
style say_dialogue:
    xpos=gui.dialogue_xpos
    ypos=gui.dialogue_ypos
"""

    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is True


@pytest.mark.parametrize(
    "source",
    [
        """\
style say_dialogue_extra:
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos
""",
        """\
style say_dialogue:
    xpos gui.dialogue_xpos_extra
    ypos gui.dialogue_ypos
""",
        """\
style say_dialogue:
    xpos gui.dialogue_xpos
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos
""",
        """\
style say_dialogue:
    xpos other # xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos
""",
    ],
)
def test_analyze_say_dialogue_style_binding_rejects_unproven_bindings(
    source: str,
) -> None:
    binding = analyze_say_dialogue_style_binding(
        source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )

    assert binding.binding_proven is False


@pytest.mark.parametrize(
    "line",
    [
        'text what id "what"\n',
        'text what id "what" style "say_dialogue"\n',
    ],
)
def test_prove_say_what_text_binding_accepts_only_standard_forms(line: str) -> None:
    prove_say_what_text_binding(line)


@pytest.mark.parametrize(
    "line",
    [
        'text custom_text id "what"\n',
        'text what id "other"\n',
        'text what id "what" style "custom_dialogue"\n',
        'text what id "what" xpos 10\n',
    ],
)
def test_prove_say_what_text_binding_rejects_custom_forms(line: str) -> None:
    with pytest.raises(EditorSourceError) as exc_info:
        prove_say_what_text_binding(line)

    assert exc_info.value.code == "STYLE_POSITION_SOURCE_UNRESOLVED"
