from __future__ import annotations

from pathlib import Path

import pytest

from renforge.editor.paths import EditorPathError, resolve_game_path
from renforge.editor.source import EditorSourceError, analyze_textbutton_statement, apply_textbutton_patch


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

