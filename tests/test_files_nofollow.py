from __future__ import annotations

from pathlib import Path

import pytest

from renforge.captures import write_project_capture
from renforge.util.files import write_atomic, write_json_atomic


def test_write_atomic_nofollow_rejects_symlink_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises((OSError, ValueError), match="symlink"):
        write_atomic(parent / "file.txt", "secret", follow_symlinks=False)

    assert list(outside.iterdir()) == []


def test_write_atomic_nofollow_rejects_leaf_symlink(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    target = parent / "file.txt"
    target.symlink_to(outside)

    with pytest.raises((OSError, ValueError), match="symlink"):
        write_atomic(target, "new", follow_symlinks=False)

    assert outside.read_text(encoding="utf-8") == "keep"


def test_write_project_capture_does_not_follow_dir_swapped_after_validation(
    monkeypatch, tmp_path: Path
) -> None:
    from renforge import captures as capmod

    project = tmp_path / "game"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    real_ensure = capmod.ensure_nofollow_directory

    def swap_after_validation(path: Path) -> Path:
        result = real_ensure(path)
        swapped = Path(result)
        for child in swapped.iterdir():
            child.unlink()
        swapped.rmdir()
        swapped.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(capmod, "ensure_nofollow_directory", swap_after_validation)

    with pytest.raises((OSError, ValueError)):
        write_project_capture(project, "frame", b"png-bytes")

    assert list(outside.iterdir()) == []
    assert not (project / ".renforge" / "captures" / "frame.png").is_file()


def test_write_json_atomic_nofollow_still_writes_inside_real_directory(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    write_json_atomic(path, {"ok": True}, follow_symlinks=False, max_bytes=1024)
    assert path.read_text(encoding="utf-8") == '{"ok":true}'
