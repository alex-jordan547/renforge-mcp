"""Deterministic regression tests for issue #50 text colour style contract."""

from __future__ import annotations

import pytest

from renforge.editor.source import (
    EditorSourceError,
    TEXT_STYLE_COLOR_MODE_LITERAL,
    analyze_text_color_style,
    apply_text_color_patch,
)


def test_analyze_text_color_style_unlocks_pure_literal_hex() -> None:
    line = (
        '    text "STYLE" color "#e22b2b" id "style_color_target" '
        "size 64 xpos 200 ypos 200\n"
    )
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    assert parsed.widget_id == "style_color_target"
    assert parsed.color == "#e22b2b"
    assert parsed.style_mode == TEXT_STYLE_COLOR_MODE_LITERAL
    assert parsed.style_lock_code is None
    assert parsed.color_span is not None
    assert line[parsed.color_span[0] : parsed.color_span[1]] == '"#e22b2b"'


def test_apply_text_color_patch_preserves_unrelated_bytes_and_form() -> None:
    line = (
        '    text "STYLE" color "#e22b2b" id "style_color_target" '
        "size 64 xpos 200 ypos 200\n"
    )
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    patched = apply_text_color_patch(
        line.encode("utf-8"),
        parsed,
        color="#2457d6",
    ).decode("utf-8")
    assert patched == (
        '    text "STYLE" color "#2457d6" id "style_color_target" '
        "size 64 xpos 200 ypos 200\n"
    )
    # Outside the colour token: identical.
    before_norm = line.replace('"#e22b2b"', "__COLOR__", 1)
    after_norm = patched.replace('"#2457d6"', "__COLOR__", 1)
    assert before_norm == after_norm


def test_apply_text_color_patch_preserves_single_quote_form() -> None:
    line = "    text 'STYLE' color '#abc' id 'style_color_target'\n"
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    assert parsed.color == "#abc"
    patched = apply_text_color_patch(
        line.encode("utf-8"),
        parsed,
        color="#def",
    ).decode("utf-8")
    assert patched == "    text 'STYLE' color '#def' id 'style_color_target'\n"


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (
            '    text "INHERIT" id "style_color_inherited" size 32 xpos 10 ypos 10\n',
            "STYLE_COLOR_NOT_DIRECTLY_AUTHORED",
        ),
        (
            '    text "EXPR" color renforge_style_expr_color id "style_color_expr"\n',
            "STYLE_COLOR_LITERAL_REQUIRED",
        ),
        (
            '    text "EXPR" color "#e22b2b" if flag else "#2457d6" id "style_color_expr"\n',
            "STYLE_COLOR_EXPRESSION_UNSUPPORTED",
        ),
        (
            '    text "BAD" color "#zzzzzz" id "style_color_bad"\n',
            "STYLE_COLOR_UNSUPPORTED_FORM",
        ),
        (
            '    text "BAD" color "red" id "style_color_bad"\n',
            "STYLE_COLOR_UNSUPPORTED_FORM",
        ),
        (
            '    text "DUP" color "#e22b2b" color "#2457d6" id "style_color_dup"\n',
            "STYLE_COLOR_DUPLICATE",
        ),
        (
            '    text "TUPLE" color (1.0, 0.0, 0.0) id "style_color_tuple"\n',
            "STYLE_COLOR_LITERAL_REQUIRED",
        ),
    ],
)
def test_analyze_text_color_style_fail_closed_matrix(line: str, code: str) -> None:
    parsed = analyze_text_color_style(line, expected_widget_id=line.split('id "')[1].split('"')[0])
    assert parsed.style_mode is None
    assert parsed.color is None
    assert parsed.color_span is None
    assert parsed.style_lock_code == code


def test_analyze_text_color_style_rejects_non_text_adapter() -> None:
    with pytest.raises(EditorSourceError) as exc:
        analyze_text_color_style(
            '    textbutton "Play" color "#e22b2b" id "start" xpos 1 ypos 2 action NullAction()\n',
            expected_widget_id="start",
        )
    assert exc.value.code == "STATEMENT_KIND_MISMATCH"


def test_analyze_text_color_style_rejects_block_form() -> None:
    with pytest.raises(EditorSourceError) as exc:
        analyze_text_color_style(
            '    text "BLOCK" color "#e22b2b" id "style_color_block":\n',
            expected_widget_id="style_color_block",
        )
    assert exc.value.code == "MULTILINE_STATEMENT_REJECTED"


def test_analyze_text_color_style_rejects_id_mismatch() -> None:
    with pytest.raises(EditorSourceError) as exc:
        analyze_text_color_style(
            '    text "STYLE" color "#e22b2b" id "other"\n',
            expected_widget_id="style_color_target",
        )
    assert exc.value.code == "ID_MISMATCH"


def test_apply_text_color_patch_refuses_locked_statement() -> None:
    line = '    text "INHERIT" id "style_color_inherited" size 32\n'
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_inherited")
    assert parsed.style_lock_code == "STYLE_COLOR_NOT_DIRECTLY_AUTHORED"
    with pytest.raises(EditorSourceError) as exc:
        apply_text_color_patch(line.encode("utf-8"), parsed, color="#2457d6")
    assert exc.value.code == "STYLE_COLOR_NOT_DIRECTLY_AUTHORED"


def test_apply_text_color_patch_refuses_unsupported_new_color() -> None:
    line = '    text "STYLE" color "#e22b2b" id "style_color_target"\n'
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    with pytest.raises(EditorSourceError) as exc:
        apply_text_color_patch(line.encode("utf-8"), parsed, color="not-a-color")
    assert exc.value.code == "STYLE_COLOR_UNSUPPORTED_FORM"


def test_apply_text_color_patch_refuses_stale_source() -> None:
    line = '    text "STYLE" color "#e22b2b" id "style_color_target"\n'
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    stale = line.replace("STYLE", "OTHER")
    with pytest.raises(EditorSourceError) as exc:
        apply_text_color_patch(stale.encode("utf-8"), parsed, color="#2457d6")
    assert exc.value.code == "STALE_SOURCE"


def test_apply_text_color_patch_preserves_hex_family_and_unicode_prefix() -> None:
    line = '    text "ÉTÉ" color "#abc" id "style_color_target"\n'
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    patched = apply_text_color_patch(line.encode("utf-8"), parsed, color="#def")
    assert patched.decode("utf-8") == '    text "ÉTÉ" color "#def" id "style_color_target"\n'
    with pytest.raises(EditorSourceError) as exc:
        apply_text_color_patch(line.encode("utf-8"), parsed, color="#ddeeff")
    assert exc.value.code == "STYLE_COLOR_HEX_FAMILY_MISMATCH"


def test_text_color_contract_does_not_route_through_coordinate_spans() -> None:
    """Colour spans must not alias xpos/ypos integer tokens."""
    line = (
        '    text "STYLE" xpos 200 ypos 180 color "#e22b2b" id "style_color_target"\n'
    )
    parsed = analyze_text_color_style(line, expected_widget_id="style_color_target")
    token = line[parsed.color_span[0] : parsed.color_span[1]]
    assert "200" not in token
    assert "180" not in token
    assert "#e22b2b" in token
    patched = apply_text_color_patch(
        line.encode("utf-8"),
        parsed,
        color="#00ff00",
    ).decode("utf-8")
    assert "xpos 200" in patched
    assert "ypos 180" in patched
    assert 'color "#00ff00"' in patched
