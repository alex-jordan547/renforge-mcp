from pathlib import Path

import pytest

from renforge.project import RenpyProject


def test_renpy_project_with_game_dir(tmp_path: Path) -> None:
    project_root = tmp_path / "valid_project"
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True)

    project = RenpyProject(project_root)

    assert project.root == project_root
    assert project.game_dir == game_dir
    assert project.cache_dir == project_root / ".renforge"
    assert project.cache_dir.is_dir()


def test_renpy_project_without_game_dir_raises(tmp_path: Path) -> None:
    project_root = tmp_path / "invalid_project"
    project_root.mkdir()

    with pytest.raises(FileNotFoundError, match="missing 'game/'"):
        RenpyProject(project_root)
