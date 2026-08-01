from __future__ import annotations

from pathlib import Path

import pytest

from renforge.editor.paths import EditorPathError, resolve_game_path
from renforge.editor.source import (
    BarStatement,
    ButtonStatement,
    EditorSourceError,
    analyze_bar_statement,
    analyze_button_statement,
    analyze_editable_statement,
    analyze_imagebutton_statement,
    analyze_textbutton_statement,
    apply_bar_patch,
    apply_button_patch,
    apply_editable_statement_patch,
    apply_imagebutton_patch,
    apply_textbutton_patch,
    peek_statement_kind,
)


def test_analyze_textbutton_statement_rejects_expressions_and_duplicates() -> None:
    with pytest.raises(EditorSourceError, match="xpos"):
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" xpos xpos_base ypos 10 action Jump("x")\n',
            expected_widget_id="start",
        )

    with pytest.raises(EditorSourceError, match="exactly one"):
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" xpos 12 xpos 20 ypos 10 action Jump("x")\n',
            expected_widget_id="start",
        )

    with pytest.raises(EditorSourceError, match="exactly one"):
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" xpos 12 ypos 10 ypos 20 action Jump("x")\n',
            expected_widget_id="start",
        )


def test_analyze_textbutton_statement_rejects_duplicate_or_mismatched_id() -> None:
    with pytest.raises(EditorSourceError, match="exactly one"):
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" id "other" xpos 12 ypos 10 action Jump("x")\n',
            expected_widget_id="start",
        )

    with pytest.raises(EditorSourceError, match="does not match"):
        analyze_textbutton_statement(
            '    textbutton "Play" id "other" xpos 12 ypos 10 action Jump("x")\n',
            expected_widget_id="start",
        )


def test_apply_textbutton_patch_preserves_all_bytes_outside_integer_tokens() -> None:
    line = '    textbutton "Play" id "start" xpos 12 ypos 10 action Jump("x")\n'
    match = analyze_textbutton_statement(line, expected_widget_id="start")
    patched = apply_textbutton_patch(line.encode("utf-8"), match, x=301, y=409).decode("utf-8")
    assert patched == '    textbutton "Play" id "start" xpos 301 ypos 409 action Jump("x")\n'


def test_resolve_game_path_rejects_alias_escape_and_symlink(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True)
    script = game_dir / "script.rpy"
    script.write_text('label start:\n    "hello"\n', encoding="utf-8")

    for bad in ("", ".", "..", "../script.rpy", "/tmp/abs.rpy", "sub\\path.rpy", "a\x00b.rpy"):
        with pytest.raises(EditorPathError):
            resolve_game_path(project_root, bad)

    link_path = game_dir / "link.rpy"
    link_path.symlink_to(script)
    with pytest.raises(EditorPathError, match="symlink"):
        resolve_game_path(project_root, "link.rpy")


def test_analyze_textbutton_statement_ignores_keywords_inside_strings_comments_and_nested_calls() -> None:
    line = (
        '    textbutton "id \\"fake\\" xpos 88 ypos 99 # comment" '
        'id "start" xpos 12 ypos 34 action Jump("xpos 1 ypos 2 id \\"bogus\\"") # xpos 222\n'
    )
    parsed = analyze_textbutton_statement(line, expected_widget_id="start")
    assert parsed.widget_id == "start"
    assert parsed.xpos == 12
    assert parsed.ypos == 34
    patched = apply_textbutton_patch(line.encode("utf-8"), parsed, x=77, y=66).decode("utf-8")
    assert 'id "start" xpos 77 ypos 66 action Jump("xpos 1 ypos 2 id \\"bogus\\"") # xpos 222' in patched
    assert 'textbutton "id \\"fake\\" xpos 88 ypos 99 # comment"' in patched


def test_analyze_textbutton_statement_rejects_statement_with_only_nested_coordinates() -> None:
    with pytest.raises(EditorSourceError, match="xpos"):
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" action Function(move_to, xpos=90, ypos=50)\n',
            expected_widget_id="start",
        )


def test_apply_textbutton_patch_preserves_non_ascii_bytes_outside_coordinate_spans() -> None:
    line = '    textbutton "Café — 東京" id "start" xpos -12 ypos 10 action NullAction()\n'
    parsed = analyze_textbutton_statement(line, expected_widget_id="start")
    patched = apply_textbutton_patch(line.encode("utf-8"), parsed, x=901, y=-7)
    assert patched.decode("utf-8") == (
        '    textbutton "Café — 東京" id "start" xpos 901 ypos -7 action NullAction()\n'
    )


def test_analyze_rejects_compound_numeric_position_expressions() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" xpos 100-20 ypos 10 action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton id "icon" idle Solid("#0f0") xpos 12 ypos 10+4 action NullAction()\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "YPOS_LITERAL_REQUIRED"


def test_analyze_button_statement_rejects_compound_numeric_position_expressions() -> None:
    invalid_headers = (
        (
            '    button id "button_target" xpos 100-20 ypos 10:\n',
            "XPOS_LITERAL_REQUIRED",
        ),
        (
            '    button id "button_target" xpos 100 ypos 10+4:\n',
            "YPOS_LITERAL_REQUIRED",
        ),
        (
            '    button id "button_target" xpos 100.5 ypos 10:\n',
            "XPOS_LITERAL_REQUIRED",
        ),
    )
    for header, expected_code in invalid_headers:
        source = "screen test_screen:\n" + header + '        text "Child"\n'
        with pytest.raises(EditorSourceError) as exc_info:
            analyze_button_statement(source, source_line=2, expected_widget_id="button_target")
        assert exc_info.value.code == expected_code

    for header in (
        '    button id "button_target" xpos 100 ypos 200:\n',
        '    button id "button_target" xpos 100 ypos 200 :\n',
    ):
        source = "screen test_screen:\n" + header + '        text "Child"\n'
        parsed = analyze_button_statement(source, source_line=2, expected_widget_id="button_target")
        assert (parsed.xpos, parsed.ypos) == (100, 200)


@pytest.mark.parametrize(
    ("source_line", "header", "expected_widget_id", "expected_code"),
    (
        (
            2,
            '    text "not_a_button":\n',
            "not_a_button",
            "STATEMENT_KIND_MISMATCH",
        ),
        (
            0,
            '    button id "button_target" xpos 120 ypos 80:\n',
            "button_target",
            "SOURCE_LINE_INVALID",
        ),
        (
            10,
            '    button id "button_target" xpos 120 ypos 80:\n',
            "button_target",
            "SOURCE_LINE_INVALID",
        ),
        (
            2,
            "    button xpos 120 ypos 80:\n",
            "button_target",
            "ID_LITERAL_REQUIRED",
        ),
        (
            2,
            '    button id "first" id "second" xpos 120 ypos 80:\n',
            "button_target",
            "ID_LITERAL_REQUIRED",
        ),
        (
            2,
            "    button id 123 xpos 120 ypos 80:\n",
            "button_target",
            "ID_LITERAL_REQUIRED",
        ),
        (
            2,
            '    button id "actual" xpos 120 ypos 80:\n',
            "expected",
            "ID_MISMATCH",
        ),
        (
            2,
            '    button id "button_target" xpos 10 xpos 20 ypos 80:\n',
            "button_target",
            "XPOS_DUPLICATE",
        ),
        (
            2,
            '    button id "button_target" xpos 10:\n',
            "button_target",
            "YPOS_DUPLICATE",
        ),
        (
            2,
            '    button id "button_target" xpos 10 ypos "not_a_number":\n',
            "button_target",
            "YPOS_LITERAL_REQUIRED",
        ),
    ),
)
def test_analyze_button_statement_rejects_invalid_headers(
    source_line: int,
    header: str,
    expected_widget_id: str,
    expected_code: str,
) -> None:
    source = "screen test_screen:\n" + header + '        text "Child"\n'
    with pytest.raises(EditorSourceError) as exc_info:
        analyze_button_statement(
            source,
            source_line=source_line,
            expected_widget_id=expected_widget_id,
        )
    assert exc_info.value.code == expected_code


def test_peek_statement_kind_reads_first_top_level_word() -> None:
    assert (
        peek_statement_kind(
            '    imagebutton id "icon" idle Solid("#0f0") xpos 1 ypos 2 action NullAction()\n'
        )
        == "imagebutton"
    )
    assert (
        peek_statement_kind('    textbutton "Play" id "start" xpos 1 ypos 2 action NullAction()\n')
        == "textbutton"
    )
    assert peek_statement_kind("    # comment only\n") is None


def test_analyze_imagebutton_statement_accepts_single_line_and_patches_spans() -> None:
    line = (
        '    imagebutton id "icon" idle Solid("#4c6ef5", xysize=(80, 48)) '
        "xpos 200 ypos 180 action NullAction()\n"
    )
    parsed = analyze_imagebutton_statement(line, expected_widget_id="icon")
    assert parsed.widget_id == "icon"
    assert parsed.xpos == 200
    assert parsed.ypos == 180
    patched = apply_imagebutton_patch(line.encode("utf-8"), parsed, x=240, y=196).decode("utf-8")
    assert patched == (
        '    imagebutton id "icon" idle Solid("#4c6ef5", xysize=(80, 48)) '
        "xpos 240 ypos 196 action NullAction()\n"
    )


def test_analyze_imagebutton_statement_rejects_textbutton_kind() -> None:
    with pytest.raises(EditorSourceError, match="imagebutton") as excinfo:
        analyze_imagebutton_statement(
            '    textbutton "Play" id "start" xpos 12 ypos 10 action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"


def test_analyze_imagebutton_statement_rejects_expressions_duplicates_and_multiline() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton id "icon" idle Solid("#0f0") xpos xpos_base ypos 10 action NullAction()\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton id "icon" idle Solid("#0f0") xpos 1 xpos 2 ypos 10 action NullAction()\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "XPOS_DUPLICATE"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton:\n        id "icon"\n        xpos 1\n        ypos 2\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "MULTILINE_STATEMENT_REJECTED"


def test_analyze_imagebutton_ignores_keywords_inside_nested_calls() -> None:
    line = (
        '    imagebutton id "icon" idle Transform("x", xpos=9) '
        "xpos 12 ypos 34 action NullAction() # xpos 99\n"
    )
    parsed = analyze_imagebutton_statement(line, expected_widget_id="icon")
    assert (parsed.xpos, parsed.ypos) == (12, 34)


def test_analyze_editable_statement_routes_kinds() -> None:
    kind, stmt = analyze_editable_statement(
        '    imagebutton id "icon" idle Solid("#0f0") xpos 3 ypos 4 action NullAction()\n',
        expected_widget_id="icon",
    )
    assert kind == "imagebutton"
    assert stmt.xpos == 3
    patched = apply_editable_statement_patch(
        '    imagebutton id "icon" idle Solid("#0f0") xpos 3 ypos 4 action NullAction()\n'.encode("utf-8"),
        kind,
        stmt,
        x=30,
        y=40,
    ).decode("utf-8")
    assert "xpos 30 ypos 40" in patched

    kind_tb, stmt_tb = analyze_editable_statement(
        '    textbutton "Play" id "start" xpos 8 ypos 9 action NullAction()\n',
        expected_widget_id="start",
    )
    assert kind_tb == "textbutton"
    assert stmt_tb.ypos == 9

    kind_bar, stmt_bar = analyze_editable_statement(
        '    bar value StaticValue(50) range 100 id "b" xpos 1 ypos 2 xsize 40 ysize 10\n',
        expected_widget_id="b",
    )
    assert kind_bar == "bar"
    assert isinstance(stmt_bar, BarStatement)
    assert stmt_bar.xpos == 1

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_editable_statement(
            '    frame id "f" xpos 1 ypos 2:\n',
            expected_widget_id="f",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_editable_statement(
            '    vbar value StaticValue(50) range 100 id "vb" xpos 1 ypos 2\n',
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "VBAR_NOT_SUPPORTED"


def test_analyze_editable_statement_reports_missing_statement_kind() -> None:
    with pytest.raises(EditorSourceError, match="does not contain a supported statement kind") as excinfo:
        analyze_editable_statement("    # comment only\n", expected_widget_id="start")
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"


def test_analyze_bar_statement_accepts_single_line_and_patches_spans() -> None:
    line = (
        '    bar value StaticValue(50) range 100 id "bar_target" '
        "xpos 200 ypos 180 xsize 240 ysize 24\n"
    )
    parsed = analyze_bar_statement(line, expected_widget_id="bar_target")
    assert isinstance(parsed, BarStatement)
    assert parsed.widget_id == "bar_target"
    assert parsed.xpos == 200
    assert parsed.ypos == 180
    patched = apply_bar_patch(line.encode("utf-8"), parsed, x=240, y=196).decode("utf-8")
    assert patched == (
        '    bar value StaticValue(50) range 100 id "bar_target" '
        "xpos 240 ypos 196 xsize 240 ysize 24\n"
    )


def test_analyze_bar_statement_preserves_bytes_outside_coordinate_spans() -> None:
    line = (
        '    bar value StaticValue(50) range 100 style "bar" id "bar_target" '
        "xpos 12 ypos 34 xsize 100 ysize 12 # keep\n"
    )
    parsed = analyze_bar_statement(line, expected_widget_id="bar_target")
    patched = apply_bar_patch(line.encode("utf-8"), parsed, x=99, y=88).decode("utf-8")
    assert patched == (
        '    bar value StaticValue(50) range 100 style "bar" id "bar_target" '
        "xpos 99 ypos 88 xsize 100 ysize 12 # keep\n"
    )


def test_analyze_bar_dispatch_and_vbar_refusal() -> None:
    assert (
        peek_statement_kind(
            '    bar value StaticValue(1) range 10 id "b" xpos 1 ypos 2\n'
        )
        == "bar"
    )
    assert (
        peek_statement_kind(
            '    vbar value StaticValue(1) range 10 id "vb" xpos 1 ypos 2\n'
        )
        == "vbar"
    )
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    vbar value StaticValue(1) range 10 id "vb" xpos 1 ypos 2\n',
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "VBAR_NOT_SUPPORTED"


def test_analyze_bar_statement_rejects_computed_and_style_and_missing_position() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos base_x ypos 10\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 10 ypos base_y\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "YPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 style "pos_style" id "b"\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "BAR_STYLE_POSITION_UNSUPPORTED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xsize 40 ysize 10\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "BAR_POSITION_NOT_DIRECTLY_AUTHORED"


def test_analyze_bar_statement_rejects_id_and_coordinate_problems() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            "    bar value StaticValue(1) range 10 xpos 1 ypos 2\n",
            expected_widget_id="b",
        )
    assert excinfo.value.code == "ID_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "a" id "b" xpos 1 ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "ID_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "other" xpos 1 ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "ID_MISMATCH"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 1 xpos 2 ypos 3\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "XPOS_DUPLICATE"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 1 ypos 2 ypos 3\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "YPOS_DUPLICATE"


def test_analyze_bar_statement_ignores_keywords_in_strings_comments_and_calls() -> None:
    line = (
        '    bar value StaticValue(50) range 100 id "bar_target" '
        'xpos 12 ypos 34 xsize 40 ysize 10 action Function(noop, xpos=9) # xpos 99\n'
    )
    parsed = analyze_bar_statement(line, expected_widget_id="bar_target")
    assert (parsed.xpos, parsed.ypos) == (12, 34)
    # Style present with literal coordinates must remain editable.
    with_style = (
        '    bar value StaticValue(50) range 100 style "bar" id "bar_target" '
        "xpos 12 ypos 34 xsize 40 ysize 10\n"
    )
    parsed_style = analyze_bar_statement(with_style, expected_widget_id="bar_target")
    assert (parsed_style.xpos, parsed_style.ypos) == (12, 34)


def test_analyze_bar_statement_rejects_multiline_block() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar:\n        id "b"\n        xpos 1\n        ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "MULTILINE_STATEMENT_REJECTED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 100-20 ypos 10\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"
def test_analyze_button_statement_patches_header_only_and_preserves_child_bytes() -> None:
    source = (
        "screen test_screen:\n"
        '    button id "button_target" xpos 120 ypos 80:\n'
        '        text "Keep this child block byte-for-byte" xpos 7\n'
        "        action NullAction()\n"
    )

    parsed = analyze_button_statement(
        source,
        source_line=2,
        expected_widget_id="button_target",
    )

    assert isinstance(parsed, ButtonStatement)
    assert parsed.widget_id == "button_target"
    assert parsed.xpos == 120
    assert parsed.ypos == 80

    patched = apply_button_patch(source.encode("utf-8"), parsed, x=301, y=409).decode("utf-8")

    assert patched == (
        "screen test_screen:\n"
        '    button id "button_target" xpos 301 ypos 409:\n'
        '        text "Keep this child block byte-for-byte" xpos 7\n'
        "        action NullAction()\n"
    )
    assert patched.splitlines(keepends=True)[2:] == source.splitlines(keepends=True)[2:]


def test_analyze_button_statement_locks_coordinates_inside_child_block() -> None:
    source = (
        "screen test_screen:\n"
        '    button id "button_target":\n'
        "        xpos 120\n"
        "        ypos 80\n"
        '        text "Child"\n'
    )

    with pytest.raises(EditorSourceError) as exc_info:
        analyze_button_statement(
            source,
            source_line=2,
            expected_widget_id="button_target",
        )

    assert exc_info.value.code == "POSITION_IN_BLOCK"


def test_analyze_button_statement_requires_literal_header_coordinates_and_child_block() -> None:
    computed = (
        "screen test_screen:\n"
        '    button id "button_target" xpos base_x ypos 80:\n'
        '        text "Child"\n'
    )
    with pytest.raises(EditorSourceError) as exc_info:
        analyze_button_statement(computed, source_line=2, expected_widget_id="button_target")
    assert exc_info.value.code == "XPOS_LITERAL_REQUIRED"

    without_child = (
        "screen test_screen:\n"
        '    button id "button_target" xpos 120 ypos 80:\n'
        '    text "Sibling"\n'
    )
    with pytest.raises(EditorSourceError) as exc_info:
        analyze_button_statement(without_child, source_line=2, expected_widget_id="button_target")
    assert exc_info.value.code == "BUTTON_BLOCK_REQUIRED"
