from __future__ import annotations

from pathlib import Path

from renforge.editor_task0_runner import (
    EDITOR_RESOURCE,
    FIXTURE_RESOURCE,
    inject_editor_task0_resources,
)


def test_task0_runner_resources_are_real_files() -> None:
    assert EDITOR_RESOURCE.is_file(), EDITOR_RESOURCE
    assert FIXTURE_RESOURCE.is_file(), FIXTURE_RESOURCE


def test_task0_runner_injects_editor_and_fixture(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    (project / "game").mkdir(parents=True)

    copied = inject_editor_task0_resources(project)

    editor = Path(copied["editor"])
    fixture = Path(copied["fixture"])
    assert editor.is_file()
    assert fixture.is_file()
    assert editor.read_text(encoding="utf-8")
    assert fixture.read_text(encoding="utf-8")
