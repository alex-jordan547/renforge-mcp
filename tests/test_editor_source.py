from __future__ import annotations

from pathlib import Path

import pytest

from renforge.editor.paths import EditorPathError, resolve_game_path
from renforge.editor.source import (
    EditorSourceError,
    analyze_editable_statement,
    analyze_imagebutton_statement,
    analyze_textbutton_statement,
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

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_editable_statement(
            '    bar id "b" value 1 range 2 xpos 1 ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"
