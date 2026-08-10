"""Unit tests for conflict-preserving source publication (step 5 CAS)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import renforge.editor.paths as editor_paths
import renforge.util.files as file_utils
from renforge.editor.paths import (
    EditorPathError,
    conditional_replace_file,
    hash_file_nofollow,
    write_exclusive_bytes,
)
from renforge.util.files import copy_regular_file_nofollow


def test_exchange_unix_uses_libc_renameat2_on_linux(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    replacement = tmp_path / "replacement"
    calls: list[tuple[object, ...]] = []

    def renameat2(*args: object) -> int:
        calls.append(args)
        return 0

    class FakeLibc:
        pass

    libc = FakeLibc()
    libc.renameat2 = renameat2  # type: ignore[attr-defined]
    monkeypatch.setattr(editor_paths.sys, "platform", "linux")
    monkeypatch.setattr(editor_paths.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    editor_paths._exchange_unix(source, replacement)

    assert calls == [(-100, bytes(source), -100, bytes(replacement), 2)]


def test_conditional_replace_exchanges_and_retains_displaced(tmp_path: Path) -> None:
    source = tmp_path / "screen.rpy"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    source.write_text("xpos 10\n", encoding="utf-8")
    write_exclusive_bytes(replacement, b"xpos 99\n")
    expected = hash_file_nofollow(source)

    result = conditional_replace_file(
        source,
        expected_sha256=expected,
        replacement_path=replacement,
        displaced_path=displaced,
    )

    assert source.read_text(encoding="utf-8") == "xpos 99\n"
    assert displaced.read_text(encoding="utf-8") == "xpos 10\n"
    assert not replacement.exists()
    assert result.displaced_sha256 == expected
    assert result.published_sha256 == hash_file_nofollow(source)


def test_nofollow_copy_opens_source_and_destination_in_binary_mode(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    payload = b"first\r\nsecond\x1aafter-eof\r\n"
    source.write_bytes(payload)
    real_open = os.open
    binary_flag = 1 << 29
    opened_flags: list[int] = []

    monkeypatch.setattr(file_utils.os, "O_BINARY", binary_flag, raising=False)

    def recording_open(path: str, flags: int, *args: object) -> int:
        opened_flags.append(flags)
        return real_open(path, flags & ~binary_flag, *args)

    monkeypatch.setattr(file_utils.os, "open", recording_open)

    copied = copy_regular_file_nofollow(
        source,
        destination,
        max_bytes=len(payload),
    )

    assert copied == len(payload)
    assert destination.read_bytes() == payload
    assert len(opened_flags) == 2
    assert all(flags & binary_flag for flags in opened_flags)


def test_conditional_replace_stale_source_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "screen.rpy"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    source.write_text("xpos 10\n", encoding="utf-8")
    write_exclusive_bytes(replacement, b"xpos 99\n")
    stale = "0" * 64

    with pytest.raises(EditorPathError) as excinfo:
        conditional_replace_file(
            source,
            expected_sha256=stale,
            replacement_path=replacement,
            displaced_path=displaced,
        )

    assert excinfo.value.code == "STALE_SOURCE"
    assert source.read_text(encoding="utf-8") == "xpos 10\n"
    assert not displaced.exists()
    assert replacement.exists()


def test_conditional_replace_rejects_existing_displaced_path(tmp_path: Path) -> None:
    source = tmp_path / "screen.rpy"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    source.write_text("xpos 10\n", encoding="utf-8")
    write_exclusive_bytes(replacement, b"xpos 99\n")
    displaced.write_text("keep", encoding="utf-8")

    with pytest.raises(EditorPathError) as excinfo:
        conditional_replace_file(
            source,
            expected_sha256=hash_file_nofollow(source),
            replacement_path=replacement,
            displaced_path=displaced,
        )

    assert excinfo.value.code == "PATH_EXISTS"
    assert source.read_text(encoding="utf-8") == "xpos 10\n"


def test_hash_file_nofollow_rejects_leaf_swapped_during_open(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen.rpy"
    backup = tmp_path / "screen.backup"
    victim = tmp_path / "victim"
    source.write_text("safe\n", encoding="utf-8")
    victim.write_text("secret\n", encoding="utf-8")
    real_open = os.open
    raced = False

    def racing_open(path: str, flags: int, *args: object) -> int:
        nonlocal raced
        if Path(path) == source and not raced:
            raced = True
            source.rename(backup)
            source.symlink_to(victim)
            try:
                return real_open(path, flags, *args)
            finally:
                source.unlink()
                backup.rename(source)
        return real_open(path, flags, *args)

    monkeypatch.setattr(file_utils.os, "open", racing_open)

    with pytest.raises(EditorPathError) as excinfo:
        hash_file_nofollow(source)

    assert excinfo.value.code == "PATH_NOT_REGULAR_FILE"
    assert source.read_text(encoding="utf-8") == "safe\n"
    assert victim.read_text(encoding="utf-8") == "secret\n"


@pytest.mark.skipif(os.name == "nt", reason="Unix normalization only")
def test_conditional_replace_never_clobbers_racing_displaced_path(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen.rpy"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    source.write_text("original\n", encoding="utf-8")
    write_exclusive_bytes(replacement, b"published\n")
    expected = hash_file_nofollow(source)
    real_exchange = editor_paths._exchange_unix

    def exchange_then_race(path: Path, replacement_path: Path) -> None:
        real_exchange(path, replacement_path)
        displaced.write_text("racer\n", encoding="utf-8")

    monkeypatch.setattr(editor_paths, "_exchange_unix", exchange_then_race)

    with pytest.raises(EditorPathError) as excinfo:
        conditional_replace_file(
            source,
            expected_sha256=expected,
            replacement_path=replacement,
            displaced_path=displaced,
        )

    assert excinfo.value.code == "SOURCE_EXCHANGE_CONFLICT"
    assert displaced.read_text(encoding="utf-8") == "racer\n"
    assert replacement.read_text(encoding="utf-8") == "original\n"
