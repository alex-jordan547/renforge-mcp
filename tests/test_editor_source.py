from __future__ import annotations

from pathlib import Path

import pytest

from renforge.editor.paths import EditorPathError, resolve_game_path
from renforge.editor.source import (
    BarStatement,
    ButtonStatement,
    EditorSourceError,
    SliderStatement,
    TextbuttonStatement,
    VbarStatement,
    analyze_bar_statement,
    analyze_button_statement,
    analyze_editable_statement,
    analyze_imagebutton_statement,
    analyze_slider_statement,
    analyze_textbutton_block_statement,
    analyze_textbutton_statement,
    analyze_vbar_statement,
    apply_bar_patch,
    apply_button_patch,
    apply_editable_statement_patch,
    apply_imagebutton_patch,
    apply_slider_patch,
    apply_textbutton_patch,
    apply_vbar_patch,
    is_slider_style_bar_line,
    is_textbutton_block_header,
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


def test_analyze_textbutton_pos_literal_accepts_and_patches_pair_only() -> None:
    line = '    textbutton "Play" id "start" pos (12, 10) action NullAction()\n'
    parsed = analyze_textbutton_statement(line, expected_widget_id="start")
    assert isinstance(parsed, TextbuttonStatement)
    assert parsed.position_mode == "pos"
    assert (parsed.xpos, parsed.ypos) == (12, 10)
    patched = apply_textbutton_patch(line.encode("utf-8"), parsed, x=301, y=409).decode("utf-8")
    assert patched == '    textbutton "Play" id "start" pos (301, 409) action NullAction()\n'
    # Form preserved — never rewritten to xpos/ypos.
    assert "xpos" not in patched
    assert "ypos" not in patched
    assert "pos (" in patched

    negative = '    textbutton "Play" id "start" pos (-12, -10) action NullAction()\n'
    neg_parsed = analyze_textbutton_statement(negative, expected_widget_id="start")
    assert (neg_parsed.xpos, neg_parsed.ypos) == (-12, -10)
    neg_patched = apply_textbutton_patch(
        negative.encode("utf-8"), neg_parsed, x=-1, y=2
    ).decode("utf-8")
    assert neg_patched == '    textbutton "Play" id "start" pos (-1, 2) action NullAction()\n'


def test_analyze_textbutton_pos_rejects_non_literal_mixed_and_duplicate() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" pos (base_x, 10) action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "POS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" pos (10+1, 10) action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "POS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" pos (1, 2) xpos 3 ypos 4 action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "POSITION_FORM_MIXED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "Play" id "start" pos (1, 2) pos (3, 4) action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "POS_DUPLICATE"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "Play" id "other" pos (1, 2) action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "ID_MISMATCH"

    kind, stmt = analyze_editable_statement(
        '    textbutton "Play" id "start" pos (8, 9) action NullAction()\n',
        expected_widget_id="start",
    )
    assert kind == "textbutton"
    assert isinstance(stmt, TextbuttonStatement)
    assert stmt.position_mode == "pos"


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


def test_analyze_textbutton_block_preserves_bytes_outside_coordinate_spans() -> None:
    source = (
        "screen test_screen:\n"
        '    textbutton "MOVE ME":\n'
        '        id "ml_target"\n'
        "        xpos 180\n"
        "        ypos 210\n"
        "        action NullAction()\n"
        '        # keep comment\n'
    )
    assert is_textbutton_block_header('    textbutton "MOVE ME":\n')
    parsed = analyze_textbutton_block_statement(
        source,
        source_line=2,
        expected_widget_id="ml_target",
    )
    assert isinstance(parsed, TextbuttonStatement)
    assert parsed.form == "block"
    assert parsed.source_line == 2
    assert (parsed.xpos, parsed.ypos) == (180, 210)
    patched = apply_textbutton_patch(source.encode("utf-8"), parsed, x=240, y=196).decode("utf-8")
    assert patched == (
        "screen test_screen:\n"
        '    textbutton "MOVE ME":\n'
        '        id "ml_target"\n'
        "        xpos 240\n"
        "        ypos 196\n"
        "        action NullAction()\n"
        '        # keep comment\n'
    )


def test_analyze_textbutton_block_rejects_header_positions_and_expressions() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_block_statement(
            "screen s:\n"
            '    textbutton "X" id "ml" xpos 1 ypos 2:\n'
            "        action NullAction()\n",
            source_line=2,
            expected_widget_id="ml",
        )
    assert excinfo.value.code == "POSITION_ON_HEADER_UNSUPPORTED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_block_statement(
            "screen s:\n"
            '    textbutton "X":\n'
            '        id "ml"\n'
            "        xpos base_x\n"
            "        ypos 10\n"
            "        action NullAction()\n",
            source_line=2,
            expected_widget_id="ml",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_block_statement(
            "screen s:\n"
            '    textbutton "X":\n'
            '        id "ml"\n'
            "        xpos 10+4\n"
            "        ypos 10\n"
            "        action NullAction()\n",
            source_line=2,
            expected_widget_id="ml",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_statement(
            '    textbutton "MOVE ME":\n',
            expected_widget_id="ml",
        )
    assert excinfo.value.code == "MULTILINE_STATEMENT_REJECTED"


@pytest.mark.parametrize(
    ("block_body", "expected_code"),
    (
        (
            '        xpos 1\n        ypos 2\n        action NullAction()\n',
            "ID_LITERAL_REQUIRED",
        ),
        (
            '        id "a"\n        id "ml"\n        xpos 1\n        ypos 2\n'
            "        action NullAction()\n",
            "ID_LITERAL_REQUIRED",
        ),
        (
            '        id "other"\n        xpos 1\n        ypos 2\n        action NullAction()\n',
            "ID_MISMATCH",
        ),
        (
            '        id "ml"\n        ypos 2\n        action NullAction()\n',
            "XPOS_LITERAL_REQUIRED",
        ),
        (
            '        id "ml"\n        xpos 1\n        action NullAction()\n',
            "YPOS_LITERAL_REQUIRED",
        ),
        (
            '        id "ml"\n        xpos 1\n        xpos 2\n        ypos 3\n'
            "        action NullAction()\n",
            "XPOS_DUPLICATE",
        ),
        (
            '        id "ml"\n        xpos 1\n        ypos 2\n        ypos 3\n'
            "        action NullAction()\n",
            "YPOS_DUPLICATE",
        ),
        (
            '        id "ml"\n        xpos 1\n        ypos base_y\n        action NullAction()\n',
            "YPOS_LITERAL_REQUIRED",
        ),
    ),
)
def test_analyze_textbutton_block_rejects_id_and_coordinate_problems(
    block_body: str,
    expected_code: str,
) -> None:
    source = 'screen s:\n    textbutton "X":\n' + block_body
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_textbutton_block_statement(
            source,
            source_line=2,
            expected_widget_id="ml",
        )
    assert excinfo.value.code == expected_code


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

    bar_line = '    bar value StaticValue(50) range 100 id "b" xpos 1 ypos 2 xsize 40 ysize 10\n'
    kind_bar, stmt_bar = analyze_editable_statement(
        bar_line,
        expected_widget_id="b",
    )
    assert kind_bar == "bar"
    assert isinstance(stmt_bar, BarStatement)
    assert stmt_bar.xpos == 1
    patched_bar = apply_editable_statement_patch(
        bar_line.encode("utf-8"),
        kind_bar,
        stmt_bar,
        x=11,
        y=22,
    ).decode("utf-8")
    assert patched_bar == (
        '    bar value StaticValue(50) range 100 id "b" xpos 11 ypos 22 xsize 40 ysize 10\n'
    )
    with pytest.raises(EditorSourceError) as excinfo:
        apply_editable_statement_patch(
            bar_line.encode("utf-8"),
            "bar",
            analyze_textbutton_statement(
                '    textbutton "Play" id "start" xpos 8 ypos 9 action NullAction()\n',
                expected_widget_id="start",
            ),
            x=1,
            y=2,
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_editable_statement(
            '    frame id "f" xpos 1 ypos 2:\n',
            expected_widget_id="f",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"

    vbar_line = (
        '    vbar value StaticValue(50) range 100 id "vb" xpos 1 ypos 2 xsize 10 ysize 40\n'
    )
    kind_vbar, stmt_vbar = analyze_editable_statement(
        vbar_line,
        expected_widget_id="vb",
    )
    assert kind_vbar == "vbar"
    assert isinstance(stmt_vbar, VbarStatement)
    patched_vbar = apply_editable_statement_patch(
        vbar_line.encode("utf-8"),
        kind_vbar,
        stmt_vbar,
        x=11,
        y=22,
    ).decode("utf-8")
    assert patched_vbar == (
        '    vbar value StaticValue(50) range 100 id "vb" xpos 11 ypos 22 xsize 10 ysize 40\n'
    )
    with pytest.raises(EditorSourceError) as excinfo:
        apply_editable_statement_patch(
            vbar_line.encode("utf-8"),
            "vbar",
            analyze_bar_statement(
                '    bar value StaticValue(50) range 100 id "b" xpos 1 ypos 2 xsize 10 ysize 40\n',
                expected_widget_id="b",
            ),
            x=1,
            y=2,
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"

    slider_line = (
        '    bar value StaticValue(50) range 100 style "slider" id "sl" '
        "xpos 1 ypos 2 xsize 40 ysize 10\n"
    )
    kind_slider, stmt_slider = analyze_editable_statement(
        slider_line,
        expected_widget_id="sl",
    )
    assert kind_slider == "slider"
    assert isinstance(stmt_slider, SliderStatement)
    patched_slider = apply_editable_statement_patch(
        slider_line.encode("utf-8"),
        kind_slider,
        stmt_slider,
        x=11,
        y=22,
    ).decode("utf-8")
    assert patched_slider == (
        '    bar value StaticValue(50) range 100 style "slider" id "sl" '
        "xpos 11 ypos 22 xsize 40 ysize 10\n"
    )
    with pytest.raises(EditorSourceError) as excinfo:
        apply_editable_statement_patch(
            slider_line.encode("utf-8"),
            "slider",
            analyze_bar_statement(
                '    bar value StaticValue(50) range 100 id "b" xpos 1 ypos 2 xsize 10 ysize 40\n',
                expected_widget_id="b",
            ),
            x=1,
            y=2,
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"


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


def test_analyze_bar_statement_accepts_negative_coordinates_and_patches_spans() -> None:
    line = (
        '    bar value StaticValue(50) range 100 id "bar_target" '
        "xpos -10 ypos -20 xsize 240 ysize 24\n"
    )
    parsed = analyze_bar_statement(line, expected_widget_id="bar_target")
    assert isinstance(parsed, BarStatement)
    assert parsed.widget_id == "bar_target"
    assert parsed.xpos == -10
    assert parsed.ypos == -20
    patched = apply_bar_patch(line.encode("utf-8"), parsed, x=-30, y=-40).decode("utf-8")
    assert patched == (
        '    bar value StaticValue(50) range 100 id "bar_target" '
        "xpos -30 ypos -40 xsize 240 ysize 24\n"
    )


def test_analyze_bar_statement_rejects_keyword_expression_coordinates() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 100 if flag else 20 ypos 10\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_bar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 10 ypos 100 or base_y\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "YPOS_LITERAL_REQUIRED"


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


def test_analyze_bar_and_vbar_dispatch_independently() -> None:
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
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 1 ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"


def test_analyze_vbar_statement_accepts_single_line_and_preserves_other_bytes() -> None:
    line = (
        '    vbar value StaticValue(50) range 100 style "vbar" id "vbar_target" '
        "xpos -10 ypos -20 xsize 24 ysize 200 # keep\n"
    )
    parsed = analyze_vbar_statement(line, expected_widget_id="vbar_target")
    assert isinstance(parsed, VbarStatement)
    assert (parsed.xpos, parsed.ypos) == (-10, -20)

    patched = apply_vbar_patch(line.encode("utf-8"), parsed, x=30, y=40).decode("utf-8")
    assert patched == (
        '    vbar value StaticValue(50) range 100 style "vbar" id "vbar_target" '
        "xpos 30 ypos 40 xsize 24 ysize 200 # keep\n"
    )


@pytest.mark.parametrize(
    ("line", "expected_code"),
    (
        (
            '    vbar value StaticValue(1) range 10 id "vb" xpos base_x ypos 10\n',
            "XPOS_LITERAL_REQUIRED",
        ),
        (
            '    vbar value StaticValue(1) range 10 id "vb" xpos 10 ypos base_y\n',
            "YPOS_LITERAL_REQUIRED",
        ),
        (
            '    vbar value StaticValue(1) range 10 style "pos_style" id "vb"\n',
            "BAR_STYLE_POSITION_UNSUPPORTED",
        ),
        (
            '    vbar value StaticValue(1) range 10 id "vb" xsize 24 ysize 200\n',
            "BAR_POSITION_NOT_DIRECTLY_AUTHORED",
        ),
        (
            '    vbar value StaticValue(1) range 10 id "vb" xpos 1 ypos 2:\n',
            "MULTILINE_STATEMENT_REJECTED",
        ),
        (
            '    vbar value StaticValue(1) range 10 id "vb" xpos 1 ypos 2\n'
            "        changed NullAction()\n",
            "MULTILINE_STATEMENT_REJECTED",
        ),
    ),
)
def test_analyze_vbar_statement_reuses_proven_position_lock_contract(
    line: str,
    expected_code: str,
) -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(line, expected_widget_id="vb")
    assert excinfo.value.code == expected_code


def test_analyze_vbar_statement_rejects_id_and_coordinate_problems() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(
            "    vbar value StaticValue(1) range 10 xpos 1 ypos 2\n",
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "ID_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(
            '    vbar value StaticValue(1) range 10 id "a" id "vb" xpos 1 ypos 2\n',
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "ID_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(
            '    vbar value StaticValue(1) range 10 id "other" xpos 1 ypos 2\n',
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "ID_MISMATCH"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(
            '    vbar value StaticValue(1) range 10 id "vb" xpos 1 xpos 2 ypos 3\n',
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "XPOS_DUPLICATE"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_vbar_statement(
            '    vbar value StaticValue(1) range 10 id "vb" xpos 1 ypos 2 ypos 3\n',
            expected_widget_id="vb",
        )
    assert excinfo.value.code == "YPOS_DUPLICATE"


def test_analyze_slider_statement_accepts_single_line_and_preserves_other_bytes() -> None:
    line = (
        '    bar value StaticValue(50) range 100 style "slider" id "slider_target" '
        "xpos -10 ypos -20 xsize 240 ysize 24 # keep\n"
    )
    assert is_slider_style_bar_line(line)
    parsed = analyze_slider_statement(line, expected_widget_id="slider_target")
    assert isinstance(parsed, SliderStatement)
    assert (parsed.xpos, parsed.ypos) == (-10, -20)

    patched = apply_slider_patch(line.encode("utf-8"), parsed, x=30, y=40).decode("utf-8")
    assert patched == (
        '    bar value StaticValue(50) range 100 style "slider" id "slider_target" '
        "xpos 30 ypos 40 xsize 240 ysize 24 # keep\n"
    )


def test_analyze_slider_dispatch_uses_bar_plus_style_slider() -> None:
    # Ren'Py has no screen-language "slider" keyword; peeks as bar.
    assert (
        peek_statement_kind(
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 1 ypos 2\n'
        )
        == "bar"
    )
    assert is_slider_style_bar_line(
        '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 1 ypos 2\n'
    )
    assert not is_slider_style_bar_line(
        '    bar value StaticValue(1) range 10 id "b" xpos 1 ypos 2\n'
    )
    assert not is_slider_style_bar_line(
        '    bar value StaticValue(1) range 10 style "bar" id "b" xpos 1 ypos 2\n'
    )
    # Computed style expressions must not masquerade as the slider adapter.
    assert not is_slider_style_bar_line(
        '    bar value StaticValue(1) range 10 style "slider" if flag else "bar" '
        'id "sl" xpos 1 ypos 2\n'
    )
    assert not is_slider_style_bar_line(
        '    bar value StaticValue(1) range 10 style "slider" or "bar" '
        'id "sl" xpos 1 ypos 2\n'
    )
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 style "slider" if flag else "bar" '
            'id "sl" xpos 1 ypos 2\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"
    # Dispatch falls through to plain bar, not slider, for style expressions.
    kind_expr, stmt_expr = analyze_editable_statement(
        '    bar value StaticValue(1) range 10 style "slider" if flag else "bar" '
        'id "sl" xpos 1 ypos 2\n',
        expected_widget_id="sl",
    )
    assert kind_expr == "bar"
    assert isinstance(stmt_expr, BarStatement)
    # Bare keyword "slider" is not a supported statement form.
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    slider value StaticValue(1) range 10 id "sl" xpos 1 ypos 2\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"
    # bar without style "slider" is not the slider adapter.
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 id "b" xpos 1 ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"
    # Dispatch routes bar+style "slider" to dedicated slider path.
    kind, stmt = analyze_editable_statement(
        '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 1 ypos 2\n',
        expected_widget_id="sl",
    )
    assert kind == "slider"
    assert isinstance(stmt, SliderStatement)
    # Plain bar stays on the bar path.
    kind_bar, stmt_bar = analyze_editable_statement(
        '    bar value StaticValue(1) range 10 id "b" xpos 1 ypos 2\n',
        expected_widget_id="b",
    )
    assert kind_bar == "bar"
    assert isinstance(stmt_bar, BarStatement)


@pytest.mark.parametrize(
    ("line", "expected_code"),
    (
        (
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos base_x ypos 10\n',
            "XPOS_LITERAL_REQUIRED",
        ),
        (
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 10 ypos base_y\n',
            "YPOS_LITERAL_REQUIRED",
        ),
        (
            '    bar value StaticValue(1) range 10 style "slider" id "sl"\n',
            "BAR_STYLE_POSITION_UNSUPPORTED",
        ),
        (
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xsize 240 ysize 24\n',
            "BAR_STYLE_POSITION_UNSUPPORTED",
        ),
        (
            # Block form is rejected before the slider-style identity check.
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 1 ypos 2:\n',
            "STATEMENT_KIND_MISMATCH",
        ),
        (
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 100-20 ypos 10\n',
            "XPOS_LITERAL_REQUIRED",
        ),
        (
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 10 ypos 10+4\n',
            "YPOS_LITERAL_REQUIRED",
        ),
    ),
)
def test_analyze_slider_statement_reuses_proven_position_lock_contract(
    line: str,
    expected_code: str,
) -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(line, expected_widget_id="sl")
    assert excinfo.value.code == expected_code


def test_analyze_slider_statement_rejects_id_and_coordinate_problems() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 style "slider" xpos 1 ypos 2\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "ID_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 style "slider" id "a" id "sl" xpos 1 ypos 2\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "ID_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 style "slider" id "other" xpos 1 ypos 2\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "ID_MISMATCH"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 1 xpos 2 ypos 3\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "XPOS_DUPLICATE"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_slider_statement(
            '    bar value StaticValue(1) range 10 style "slider" id "sl" xpos 1 ypos 2 ypos 3\n',
            expected_widget_id="sl",
        )
    assert excinfo.value.code == "YPOS_DUPLICATE"


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
