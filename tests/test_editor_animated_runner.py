from __future__ import annotations

from pathlib import Path

from renforge.editor_animated_runner import inject_editor_animated_resources


def test_inject_editor_animated_resources(tmp_path: Path) -> None:
    target = inject_editor_animated_resources(tmp_path)
    assert target.exists()
    assert target.name == "zz_renforge_editor_animated_fixture.rpy"
    assert "screen renforge_editor_animated_fixture():" in target.read_text(encoding="utf-8")
