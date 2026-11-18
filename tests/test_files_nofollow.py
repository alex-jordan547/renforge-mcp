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


def _junction(target: Path, link: Path) -> None:
    winapi = pytest.importorskip("_winapi")
    if not hasattr(winapi, "CreateJunction"):
        pytest.skip("needs _winapi.CreateJunction")
    winapi.CreateJunction(str(target), str(link))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junctions")
def test_open_regular_at_nofollow_refuses_a_junctioned_directory_on_windows(tmp_path: Path) -> None:
    # The Windows path checked only the leaf, so a junction on the way was followed.
    outside = tmp_path / "outside"
    (outside / "inner").mkdir(parents=True)
    (outside / "inner" / "a.save").write_bytes(b"SECRET")
    linked = tmp_path / "saves"
    _junction(outside, linked)

    with pytest.raises(ValueError):
        open_regular_at_nofollow(linked, "a.save")
    with pytest.raises(ValueError):
        open_regular_at_nofollow(linked / "inner", "a.save")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junctions")
def test_replace_file_at_nofollow_refuses_a_junctioned_directory_on_windows(tmp_path: Path) -> None:
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "dest"
    _junction(outside, linked)

    fd, st = _source_fd(source)
    try:
        with pytest.raises(ValueError):
            replace_file_at_nofollow(
                linked, "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=1024
            )
    finally:
        os.close(fd)
    assert os.listdir(outside) == []


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows copy sets timestamps by handle")
def test_replace_file_at_nofollow_sets_the_mtime_before_publishing_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Windows copy used to publish, then set the mtime by pathname: a
    # failure there left a published file out of the import's receipt, and a
    # folder swapped for a junction redirected it.
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    os.utime(source, ns=(1_600_000_000_123_456_700, 1_600_000_000_123_456_700))
    dest = tmp_path / "dest"
    dest.mkdir()

    def no_pathname_utime(*_args, **_kwargs):
        raise AssertionError("timestamps must be set on the temp handle")

    monkeypatch.setattr(os, "utime", no_pathname_utime)
    fd, st = _source_fd(source)
    try:
        replace_file_at_nofollow(
            dest, "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=1024
        )
    finally:
        os.close(fd)

    assert os.listdir(dest) == ["a.save"]
    assert (dest / "a.save").stat().st_mtime_ns == source.stat().st_mtime_ns


def test_replace_file_at_nofollow_never_recreates_a_folder_removed_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A session stopped between the caller's directory check and the write:
    # the Windows copy recreated its root and savedir, leaving a copy of the
    # player's save in a folder nothing cleans up.
    monkeypatch.setattr(files_module, "_require_directory", lambda _path: None)
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")

    fd, st = _source_fd(source)
    try:
        with pytest.raises(OSError):
            replace_file_at_nofollow(
                tmp_path / "session" / "saves",
                "a.save",
                fd,
                mode=0o644,
                mtime_ns=st.st_mtime_ns,
                max_bytes=1024,
            )
    finally:
        os.close(fd)
    assert not (tmp_path / "session").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX without dir_fd")
def test_replace_file_at_nofollow_refuses_without_dir_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without dir_fd nothing can anchor the copy, so it is refused outright
    # (reads keep their leaf-only fallback).
    monkeypatch.setattr(files_module, "_posix_dir_fd_supported", lambda: False)
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    dest = tmp_path / "dest"
    dest.mkdir()

    fd, st = _source_fd(source)
    try:
        with pytest.raises(OSError, match="dir_fd"):
            replace_file_at_nofollow(
                dest, "a.save", fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=1024
            )
    finally:
        os.close(fd)
    assert os.listdir(dest) == []
    read_fd, _st = open_regular_at_nofollow(tmp_path, "source.save")
    os.close(read_fd)


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "fallback"])
def test_scan_directory_nofollow_skips_only_an_entry_it_cannot_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dir_fd: bool
) -> None:
    # One unreadable entry emptied the whole listing.
    if dir_fd and (os.name == "nt" or not files_module._posix_dir_fd_supported()):
        pytest.skip("needs POSIX dir_fd support")
    if not dir_fd and os.name == "nt":
        pytest.skip("Windows enumerates at a handle, not by path")
    monkeypatch.setattr(files_module, "_posix_dir_fd_supported", lambda: dir_fd)
    folder = tmp_path / "saves"
    folder.mkdir()
    (folder / "a.save").write_bytes(b"A")
    (folder / "b.save").write_bytes(b"B")
    real_lstat_at, real_lstat = files_module._lstat_at_posix, os.lstat

    def denied(name: str) -> None:
        if os.path.basename(str(name)) == "b.save":
            raise PermissionError(errno.EACCES, "denied", str(name))

    def lstat_at(dir_fd_value: int, name: str):
        denied(name)
        return real_lstat_at(dir_fd_value, name)

    def lstat(path, *args, **kwargs):
        denied(path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(files_module, "_lstat_at_posix", lstat_at)
    monkeypatch.setattr(os, "lstat", lstat)

    assert [name for name, _st in scan_directory_nofollow(folder)] == ["a.save"]


def _full_dir_record(name: str, attributes: int, *, size: int = 0, mtime_ns: int = 0, last: bool = False) -> bytes:
    encoded = name.encode("utf-16-le", "surrogatepass")
    filetime = mtime_ns // 100 + files_module._WIN_EPOCH_AS_FILETIME
    length = files_module._WIN_FULL_DIR_INFO.size + len(encoded)
    padded = (length + 7) // 8 * 8
    header = files_module._WIN_FULL_DIR_INFO.pack(
        0 if last else padded, 0, filetime, filetime, filetime, filetime,
        size, size, attributes, len(encoded), 0,
    )
    return (header + encoded).ljust(padded, b"\0")


def test_windows_directory_records_parse_like_lstat() -> None:
    # The Windows listing reads entries from a pinned handle, so their
    # metadata comes from these records rather than a stat by path.
    raw = b"".join([
        _full_dir_record(".", 0x10),
        _full_dir_record("..", 0x10),
        _full_dir_record("1-1-LT1.save", 0x20, size=4, mtime_ns=1_600_000_000_123_456_700),
        _full_dir_record("sub", 0x10),
        _full_dir_record("linked", 0x10 | 0x400),
        _full_dir_record("\U0001F600-LT1.save", 0x20 | 0x1, size=2, last=True),
    ])

    entries = dict(files_module._win_parse_full_dir_info(raw + b"\0" * 64))

    assert list(entries) == ["1-1-LT1.save", "sub", "linked", "\U0001F600-LT1.save"]
    slot = entries["1-1-LT1.save"]
    assert stat.S_ISREG(slot.st_mode) and slot.st_size == 4
    assert slot.st_mtime_ns == 1_600_000_000_123_456_700
    assert slot.st_mtime == pytest.approx(1_600_000_000.1234567)
    assert stat.S_ISDIR(entries["sub"].st_mode)
    assert stat.S_ISLNK(entries["linked"].st_mode)
    assert stat.S_ISREG(entries["\U0001F600-LT1.save"].st_mode)
    assert not entries["\U0001F600-LT1.save"].st_mode & 0o222


def test_windows_names_count_utf16_units() -> None:
    # NtCreateFile and the rename take lengths in UTF-16 units; counting code
    # points cut a name with an emoji short (or overflowed the rename buffer).
    assert files_module._utf16_units("1-1-LT1.save") == 12
    assert files_module._utf16_units("\U0001F600-LT1.save") == 11


def test_a_slot_name_with_an_emoji_round_trips(tmp_path: Path) -> None:
    name = "\U0001F600-1-LT1.save"
    source = tmp_path / "source.save"
    source.write_bytes(b"SLOT")
    dest = tmp_path / "dest"
    dest.mkdir()

    fd, st = _source_fd(source)
    try:
        replace_file_at_nofollow(dest, name, fd, mode=0o644, mtime_ns=st.st_mtime_ns, max_bytes=1024)
    finally:
        os.close(fd)
    assert os.listdir(dest) == [name]
    read_fd, _st = open_regular_at_nofollow(dest, name)
    try:
        assert os.read(read_fd, 16) == b"SLOT"
    finally:
        os.close(read_fd)
    assert [entry for entry, _st in scan_directory_nofollow(dest)] == [name]


@pytest.mark.parametrize(("writable", "listable"), [(False, False), (False, True), (True, False)])
def test_windows_read_chains_need_only_traverse_on_the_way(
    monkeypatch: pytest.MonkeyPatch, writable: bool, listable: bool
) -> None:
    # A read opened every component with list access, so a traverse-only
    # ancestor (store-installed games) denied a read that worked by path.
    opened: list[tuple[str, int]] = []

    def nt_create(*, root, name, access, disposition, options, attributes=0, share=0):
        opened.append((name, access))
        return len(opened) + 100

    class _Info:
        dwFileAttributes = files_module._WIN_FILE_ATTRIBUTE_DIRECTORY

    monkeypatch.setattr(files_module, "_open_directory_nofollow_windows", lambda *_a, **_k: 1)
    monkeypatch.setattr(files_module, "_win_nt_create", nt_create)
    monkeypatch.setattr(files_module, "_win_reject_reparse_handle", lambda *_a, **_k: _Info)
    monkeypatch.setattr(files_module, "_win_close", lambda _handle: None)

    files_module._open_directory_chain_nofollow_windows(
        Path("/Program Files/Apps/game/saves"), writable=writable, listable=listable
    )

    lists = {name: bool(access & files_module._WIN_FILE_LIST_DIRECTORY) for name, access in opened}
    adds = [name for name, access in opened if access & files_module._WIN_FILE_ADD_FILE]
    if writable:
        # Unchanged: list access everywhere, the last folder reopened writable.
        assert all(lists.values())
        assert adds == ["saves"]
    else:
        assert lists == {"Program Files": False, "Apps": False, "game": False, "saves": listable}
        assert adds == []
    for _name, access in opened:
        assert access & files_module._WIN_FILE_TRAVERSE
        assert access & files_module._WIN_FILE_READ_ATTRIBUTES


def test_windows_nt_failures_keep_the_errors_callers_tell_apart() -> None:
    missing = files_module._win_nt_status_error(-0x3FFFFFCC, "NtCreateFile failed", "a.save")
    assert isinstance(missing, FileNotFoundError)
    assert "0xC0000034" in str(missing)
    assert isinstance(files_module._win_nt_status_error(0xC0000103 - (1 << 32), "x"), NotADirectoryError)


@pytest.mark.skipif(sys.platform != "win32", reason="NTSTATUS to Win32 mapping")
def test_windows_nt_access_denied_is_a_permission_error() -> None:
    error = files_module._win_nt_status_error(0xC0000022 - (1 << 32), "NtCreateFile failed", "a.save")
    assert isinstance(error, PermissionError)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junctions")
def test_scan_directory_nofollow_lists_at_a_handle_on_windows(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.save").write_bytes(b"SECRET")
    folder = tmp_path / "saves"
    folder.mkdir()
    (folder / "a.save").write_bytes(b"SLOT")
    _junction(outside, folder / "linked")

    entries = dict(scan_directory_nofollow(folder))
    assert sorted(entries) == ["a.save", "linked"]
    assert stat.S_ISREG(entries["a.save"].st_mode) and entries["a.save"].st_size == 4
    assert entries["a.save"].st_mtime_ns == (folder / "a.save").stat().st_mtime_ns
    assert not stat.S_ISREG(entries["linked"].st_mode)
    with pytest.raises(ValueError):
        scan_directory_nofollow(folder / "linked")
