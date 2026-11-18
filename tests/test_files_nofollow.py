from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

import pytest

from renforge.captures import write_project_capture
from renforge.util import files as files_module
from renforge.util.files import (
    make_directory_at_nofollow,
    open_regular_at_nofollow,
    replace_file_at_nofollow,
    scan_directory_nofollow,
    write_atomic,
    write_json_atomic,
)


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


def test_write_project_capture_does_not_follow_ancestor_swapped_after_validation(
    monkeypatch, tmp_path: Path
) -> None:
    from renforge import captures as capmod

    project = tmp_path / "game"
    project.mkdir()
    outside = tmp_path / "outside"
    (outside / "captures").mkdir(parents=True)
    real_ensure = capmod.ensure_nofollow_directory

    def swap_ancestor_after_validation(path: Path) -> Path:
        result = real_ensure(path)
        renforge_dir = Path(result).parent
        renforge_dir.rename(project / ".renforge-held")
        renforge_dir.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(capmod, "ensure_nofollow_directory", swap_ancestor_after_validation)

    with pytest.raises((OSError, ValueError)):
        write_project_capture(project, "frame", b"png-bytes")

    assert not (outside / "captures" / "frame.png").exists()


def test_write_json_atomic_nofollow_still_writes_inside_real_directory(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    write_json_atomic(path, {"ok": True}, follow_symlinks=False, max_bytes=1024)
    assert path.read_text(encoding="utf-8") == '{"ok":true}'


def _source_fd(path: Path) -> tuple[int, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    return fd, os.fstat(fd)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_open_regular_at_nofollow_rejects_a_leaf_symlink(tmp_path: Path) -> None:
    secret = tmp_path / "secret.save"
    secret.write_bytes(b"SECRET")
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "a.save").symlink_to(secret)

    with pytest.raises(OSError):
        open_regular_at_nofollow(saves, "a.save")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_open_regular_at_nofollow_rejects_a_linked_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.save").write_bytes(b"SECRET")
    linked = tmp_path / "saves"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises((OSError, ValueError)):
        open_regular_at_nofollow(linked, "a.save")


def test_open_regular_at_nofollow_reads_a_regular_file(tmp_path: Path) -> None:
    (tmp_path / "a.save").write_bytes(b"SLOT")
    fd, st = open_regular_at_nofollow(tmp_path, "a.save")
    try:
        assert stat.S_ISREG(st.st_mode)
        assert os.read(fd, 16) == b"SLOT"
    finally:
        os.close(fd)
    with pytest.raises(FileNotFoundError):
        open_regular_at_nofollow(tmp_path, "missing.save")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_replace_file_at_nofollow_replaces_a_link_without_following(tmp_path: Path) -> None:
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    victim = tmp_path / "victim"
    victim.write_bytes(b"VICTIM")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "a.save").symlink_to(victim)

    fd, st = _source_fd(source)
    try:
        copied = replace_file_at_nofollow(
            dest, "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=1024
        )
    finally:
        os.close(fd)

    assert copied == 4
    assert victim.read_bytes() == b"VICTIM"
    assert not (dest / "a.save").is_symlink() and (dest / "a.save").read_bytes() == b"SLOT"
    assert sorted(os.listdir(dest)) == ["a.save"]


def test_replace_file_at_nofollow_keeps_mode_and_mtime(tmp_path: Path) -> None:
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    os.chmod(source, 0o640)
    os.utime(source, ns=(1_600_000_000_123_456_789, 1_600_000_000_123_456_789))
    dest = tmp_path / "dest"
    dest.mkdir()

    fd, st = _source_fd(source)
    try:
        replace_file_at_nofollow(
            dest,
            "a.save",
            fd,
            mode=stat.S_IMODE(st.st_mode),
            mtime_ns=st.st_mtime_ns,
            max_bytes=1024,
        )
    finally:
        os.close(fd)

    copied = (dest / "a.save").stat()
    assert copied.st_mtime_ns == source.stat().st_mtime_ns
    if sys.platform != "win32":
        assert copied.st_mode & 0o777 == source.stat().st_mode & 0o777


def test_replace_file_at_nofollow_over_quota_leaves_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source.save"
    source.write_bytes(b"X" * 64)
    dest = tmp_path / "dest"
    dest.mkdir()

    fd, st = _source_fd(source)
    try:
        with pytest.raises(OSError) as excinfo:
            replace_file_at_nofollow(
                dest, "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=16
            )
    finally:
        os.close(fd)
    assert excinfo.value.errno == errno.EFBIG
    assert os.listdir(dest) == []

    (dest / "a.save").write_bytes(b"EARLIER")
    fd, st = _source_fd(source)
    try:
        with pytest.raises(OSError):
            replace_file_at_nofollow(
                dest, "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=16
            )
    finally:
        os.close(fd)
    assert os.listdir(dest) == ["a.save"]
    assert (dest / "a.save").read_bytes() == b"EARLIER"


def test_replace_file_at_nofollow_needs_the_directory(tmp_path: Path) -> None:
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    fd, st = _source_fd(source)
    try:
        with pytest.raises(FileNotFoundError):
            replace_file_at_nofollow(
                tmp_path / "gone", "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=1024
            )
    finally:
        os.close(fd)
    assert not (tmp_path / "gone").exists()


def test_write_atomic_nofollow_handles_a_leaf_at_the_name_limit(tmp_path: Path) -> None:
    # The temp name used to be the leaf plus 22 units, so a long save name
    # overflowed the 255-unit file name limit on every platform.
    name = "s" * 240 + "-LT1.save"
    write_atomic(tmp_path / name, b"SLOT", follow_symlinks=False)

    assert os.listdir(tmp_path) == [name]
    assert (tmp_path / name).read_bytes() == b"SLOT"


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "fallback"])
def test_make_directory_tells_a_file_parent_from_a_missing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dir_fd: bool
) -> None:
    # The Windows fallback reported a file in the session root's place as a
    # stopped session; both paths must say "not a folder" instead.
    if dir_fd and (os.name == "nt" or not files_module._posix_dir_fd_supported()):
        pytest.skip("needs POSIX dir_fd support")
    monkeypatch.setattr(files_module, "_posix_dir_fd_supported", lambda: dir_fd)
    file_root = tmp_path / "session"
    file_root.write_bytes(b"not a folder")

    with pytest.raises(OSError) as raised:
        make_directory_at_nofollow(file_root / "saves")
    assert not isinstance(raised.value, FileNotFoundError)
    with pytest.raises(FileNotFoundError):
        make_directory_at_nofollow(tmp_path / "gone" / "saves")
    assert file_root.read_bytes() == b"not a folder"


def test_require_directory_tells_a_file_from_a_missing_folder(tmp_path: Path) -> None:
    # Windows reports a path under a file as missing, so the fallbacks decide
    # here rather than from the error a walk of the path happened to raise.
    file_root = tmp_path / "session"
    file_root.write_bytes(b"not a folder")

    with pytest.raises(NotADirectoryError):
        files_module._require_directory(file_root)
    with pytest.raises(FileNotFoundError):
        files_module._require_directory(tmp_path / "gone")
    files_module._require_directory(tmp_path)


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "fallback"])
def test_scan_directory_nofollow_reports_entries_without_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dir_fd: bool
) -> None:
    if dir_fd and (os.name == "nt" or not files_module._posix_dir_fd_supported()):
        pytest.skip("needs POSIX dir_fd support")
    monkeypatch.setattr(files_module, "_posix_dir_fd_supported", lambda: dir_fd)
    folder = tmp_path / "saves"
    folder.mkdir()
    (folder / "a.save").write_bytes(b"SLOT")
    os.utime(folder / "a.save", ns=(1_600_000_000_000_000_000,) * 2)
    (folder / "sub").mkdir()

    entries = dict(scan_directory_nofollow(folder))

    assert sorted(entries) == ["a.save", "sub"]
    assert stat.S_ISREG(entries["a.save"].st_mode)
    assert entries["a.save"].st_mtime_ns == 1_600_000_000_000_000_000
    assert stat.S_ISDIR(entries["sub"].st_mode)
    with pytest.raises(FileNotFoundError):
        scan_directory_nofollow(tmp_path / "gone")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "fallback"])
def test_scan_directory_nofollow_refuses_a_linked_folder_and_names_a_linked_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dir_fd: bool
) -> None:
    if dir_fd and not files_module._posix_dir_fd_supported():
        pytest.skip("needs POSIX dir_fd support")
    monkeypatch.setattr(files_module, "_posix_dir_fd_supported", lambda: dir_fd)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.save").write_bytes(b"SECRET")
    linked = tmp_path / "saves"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        scan_directory_nofollow(linked)

    real = tmp_path / "real"
    real.mkdir()
    (real / "a.save").symlink_to(outside / "secret.save")
    entries = dict(scan_directory_nofollow(real))
    assert stat.S_ISLNK(entries["a.save"].st_mode)
