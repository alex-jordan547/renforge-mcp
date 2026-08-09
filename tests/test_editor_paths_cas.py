"""Unit tests for conflict-preserving source publication (step 5 CAS)."""

from __future__ import annotations

from pathlib import Path

import pytest

import renforge.editor.paths as editor_paths
from renforge.editor.paths import (
    EditorPathError,
    conditional_replace_file,
    hash_file_nofollow,
    write_exclusive_bytes,
)


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
