from pathlib import Path

import pytest

from renforge.project import RenpyProject, discover_project_from


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


def test_discover_project_walks_up_from_a_nested_directory(tmp_path: Path) -> None:
    project_root = tmp_path / "my-game"
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text("label start:\n    return\n")
    nested = game_dir / "images" / "sprites"
    nested.mkdir(parents=True)

    assert discover_project_from(nested) == project_root.resolve()


def test_discover_project_ignores_a_game_dir_without_scripts(tmp_path: Path) -> None:
    # A bare "game" folder (e.g. an assets directory) is not a project.
    (tmp_path / "game").mkdir()

    assert discover_project_from(tmp_path) is None


def test_discover_project_returns_none_outside_any_project(tmp_path: Path) -> None:
    assert discover_project_from(tmp_path) is None
