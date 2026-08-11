from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..util.files import (
    fsync_directory,
    hash_file_nofollow as _hash_file_nofollow,
    sha256_bytes as sha256_bytes,
    write_exclusive_bytes as write_exclusive_bytes,
)


class EditorPathError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    if not isinstance(relative_path, str):
        raise EditorPathError("PATH_TYPE_INVALID", "path must be a string")
    if "\x00" in relative_path:
        raise EditorPathError("PATH_NUL_REJECTED", "path contains NUL byte")
    if "\\" in relative_path:
        raise EditorPathError("PATH_BACKSLASH_REJECTED", "path must use POSIX separators")
    normalized = PurePosixPath(relative_path)
    if relative_path.strip() == "" or str(normalized) in {"", "."}:
        raise EditorPathError("PATH_EMPTY_REJECTED", "path must not be empty")
    if normalized.is_absolute():
        raise EditorPathError("PATH_ABSOLUTE_REJECTED", "absolute paths are not allowed")
    if any(part in {"", ".", ".."} for part in normalized.parts):
        raise EditorPathError("PATH_ALIAS_REJECTED", "path aliases are not allowed")
    return normalized


def resolve_game_path(project_root: str | Path, relative_path: str) -> Path:
    game_root = (Path(project_root).expanduser().resolve(strict=True) / "game").resolve(strict=True)
    relative = _validate_relative_path(relative_path)

    current = game_root
    for part in relative.parts:
        current = current / part
        if _is_symlink(current):
            raise EditorPathError("PATH_SYMLINK_REJECTED", f"symlink path is not allowed: {relative_path}")
    if not current.exists():
        raise EditorPathError("PATH_NOT_FOUND", f"path does not exist: {relative_path}")
    if _is_symlink(current):
        raise EditorPathError("PATH_SYMLINK_REJECTED", f"symlink path is not allowed: {relative_path}")
    if not current.is_file():
        raise EditorPathError("PATH_NOT_REGULAR_FILE", f"path is not a regular file: {relative_path}")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(game_root):
        raise EditorPathError("PATH_ESCAPE_REJECTED", f"path escapes game root: {relative_path}")
    return resolved


def to_game_relative_path(project_root: str | Path, absolute_path: Path) -> str:
    game_root = (Path(project_root).expanduser().resolve(strict=True) / "game").resolve(strict=True)
    resolved = absolute_path.resolve(strict=True)
    if not resolved.is_relative_to(game_root):
        raise EditorPathError("PATH_ESCAPE_REJECTED", f"path escapes game root: {absolute_path}")
    return resolved.relative_to(game_root).as_posix()


def atomic_write_file(path: Path, data: bytes) -> None:
    import tempfile

    destination = path.expanduser()
    if destination.exists() and _is_symlink(destination):
        raise EditorPathError("PATH_SYMLINK_REJECTED", f"symlink path is not allowed: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, destination)
        fsync_directory(destination.parent)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _lstat_regular(path: Path) -> os.stat_result:
    try:
        st = path.lstat()
    except FileNotFoundError as exc:
        raise EditorPathError("PATH_NOT_FOUND", f"path does not exist: {path}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise EditorPathError("PATH_SYMLINK_REJECTED", f"symlink path is not allowed: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise EditorPathError("PATH_NOT_REGULAR_FILE", f"path is not a regular file: {path}")
    return st


def hash_file_nofollow(path: Path) -> str:
    """Hash a regular non-symlink file without following links."""
    _lstat_regular(path)
    try:
        return _hash_file_nofollow(path)
    except (FileNotFoundError, OSError) as exc:
        raise EditorPathError(
            "PATH_NOT_REGULAR_FILE",
            f"path changed or became unsafe while hashing: {path}",
        ) from exc


def _same_filesystem(left: Path, right: Path) -> bool:
    try:
        return left.stat().st_dev == right.stat().st_dev
    except OSError:
        try:
            return left.parent.stat().st_dev == right.parent.stat().st_dev
        except OSError:
            return False


def _copy_mode_bits(source: Path, destination: Path) -> int:
    mode = stat.S_IMODE(_lstat_regular(source).st_mode)
    os.chmod(destination, mode)
    return mode


@dataclass(frozen=True)
class ConditionalReplaceResult:
    published_sha256: str
    displaced_sha256: str
    displaced_path: Path
    source_mode: int


def _exchange_unix(path: Path, replacement_path: Path) -> None:
    """Atomically swap ``path`` and ``replacement_path`` on the same filesystem."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        # RENAME_SWAP = 0x00000002 on Darwin.
        RENAME_SWAP = 0x00000002
        rc = libc.renamex_np(
            str(path).encode("utf-8"),
            str(replacement_path).encode("utf-8"),
            RENAME_SWAP,
        )
        if rc != 0:
            err = ctypes.get_errno()
            if err in {getattr(errno, "ENOTSUP", 45), getattr(errno, "EINVAL", 22)}:
                raise EditorPathError(
                    "SOURCE_CAS_UNAVAILABLE",
                    "atomic rename exchange is unavailable on this filesystem",
                )
            raise OSError(err, os.strerror(err))
        return

    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        except (AttributeError, OSError) as exc:
            raise EditorPathError(
                "SOURCE_CAS_UNAVAILABLE",
                "atomic rename exchange is unavailable on this system",
            ) from exc
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        rc = renameat2(
            -100,  # AT_FDCWD
            os.fsencode(path),
            -100,
            os.fsencode(replacement_path),
            1 << 1,  # RENAME_EXCHANGE
        )
        if rc == 0:
            return
        err = ctypes.get_errno()
        if err in {
            getattr(errno, "ENOTSUP", 95),
            getattr(errno, "EINVAL", 22),
            getattr(errno, "ENOSYS", 38),
        }:
            raise EditorPathError(
                "SOURCE_CAS_UNAVAILABLE",
                "atomic rename exchange is unavailable on this filesystem",
            )
        raise OSError(err, os.strerror(err))

    raise EditorPathError(
        "SOURCE_CAS_UNAVAILABLE",
        f"atomic source exchange is not implemented for platform {sys.platform!r}",
    )


def _move_noreplace_unix(path: Path, destination: Path) -> None:
    """Atomically move evidence without replacing a path created by a racer."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        rc = renamex_np(os.fsencode(path), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        except (AttributeError, OSError) as exc:
            raise OSError(errno.ENOSYS, "renameat2 is unavailable") from exc
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        rc = renameat2(-100, os.fsencode(path), -100, os.fsencode(destination), 1)
    else:
        raise OSError(errno.ENOSYS, f"non-replacing move is unavailable on {sys.platform!r}")
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(destination))


def _exchange_windows(path: Path, replacement_path: Path, displaced_path: Path) -> None:
    """Replace ``path`` with ``replacement_path``, writing the previous bytes to ``displaced_path``."""
    # ReplaceFileW is the only supported Windows primitive here because it
    # atomically replaces the source while retaining its prior bytes as backup.
    # Never degrade to a read/move/replace sequence: that would violate CAS and
    # could overwrite a source update made by a concurrent editor.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    ReplaceFileW = kernel32.ReplaceFileW
    ReplaceFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    ReplaceFileW.restype = ctypes.c_int
    REPLACEFILE_WRITE_THROUGH = 0x00000001
    ok = ReplaceFileW(
        str(path),
        str(replacement_path),
        str(displaced_path),
        REPLACEFILE_WRITE_THROUGH,
        None,
        None,
    )
    if ok:
        return
    err = ctypes.get_last_error()
    raise EditorPathError(
        "SOURCE_CAS_UNAVAILABLE",
        f"ReplaceFileW failed with Win32 error {err}",
        details={"win32_error": err},
    )


def conditional_replace_file(
    path: Path,
    *,
    expected_sha256: str,
    replacement_path: Path,
    displaced_path: Path,
) -> ConditionalReplaceResult:
    """Publish ``replacement_path`` over ``path`` only if ``path`` still hashes to ``expected_sha256``.

    Uses an atomic exchange when available. Never falls back to read-then-replace.
    On a post-exchange digest race, raises ``SOURCE_EXCHANGE_CONFLICT`` and retains
    every candidate path for recovery evidence.
    """
    source = Path(path).expanduser()
    replacement = Path(replacement_path).expanduser()
    displaced = Path(displaced_path).expanduser()

    source_st = _lstat_regular(source)
    replacement_st = _lstat_regular(replacement)
    if source_st.st_dev != replacement_st.st_dev:
        raise EditorPathError(
            "SOURCE_CAS_UNAVAILABLE",
            "source and replacement must share one filesystem",
        )
    if displaced.exists() or _is_symlink(displaced):
        raise EditorPathError(
            "PATH_EXISTS",
            f"displaced path must be absent: {displaced}",
        )
    try:
        displaced.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EditorPathError("PATH_PARENT_INVALID", f"cannot create displaced parent: {exc}") from exc
    if not _same_filesystem(source, displaced.parent):
        raise EditorPathError(
            "SOURCE_CAS_UNAVAILABLE",
            "displaced path must share the source filesystem",
        )

    source_mode = _copy_mode_bits(source, replacement)
    current_sha = hash_file_nofollow(source)
    if current_sha != expected_sha256:
        raise EditorPathError(
            "STALE_SOURCE",
            "source file changed before atomic publication",
            details={
                "expected_sha256": expected_sha256,
                "actual_sha256": current_sha,
                "path": str(source),
            },
        )

    published_sha = hash_file_nofollow(replacement)

    if os.name == "nt":
        _exchange_windows(source, replacement, displaced)
        # ReplaceFileW already placed the prior bytes at displaced.
        try:
            displaced_sha = hash_file_nofollow(displaced)
        except EditorPathError as exc:
            raise EditorPathError(
                "SOURCE_EXCHANGE_CONFLICT",
                "exchange completed but displaced evidence is unreadable",
                details={
                    "expected_sha256": expected_sha256,
                    "published_sha256": published_sha,
                    "source": str(source),
                    "replacement_path": str(replacement),
                    "displaced_path": str(displaced),
                    "uncertain_paths": [str(source), str(replacement), str(displaced)],
                },
            ) from exc
        if displaced_sha != expected_sha256:
            raise EditorPathError(
                "SOURCE_EXCHANGE_CONFLICT",
                "source changed during atomic exchange; all versions retained",
                details={
                    "expected_sha256": expected_sha256,
                    "displaced_sha256": displaced_sha,
                    "published_sha256": published_sha,
                    "source": str(source),
                    "replacement_path": str(replacement),
                    "displaced_path": str(displaced),
                    "uncertain_paths": [str(source), str(replacement), str(displaced)],
                },
            )
        fsync_directory(source.parent)
        return ConditionalReplaceResult(
            published_sha256=published_sha,
            displaced_sha256=displaced_sha,
            displaced_path=displaced,
            source_mode=source_mode,
        )

    # Unix: swap in place, then normalize the old bytes to displaced_path.
    _exchange_unix(source, replacement)
    # After swap: source has published bytes; replacement has the prior source bytes.
    try:
        displaced_sha = hash_file_nofollow(replacement)
    except EditorPathError as exc:
        raise EditorPathError(
            "SOURCE_EXCHANGE_CONFLICT",
            "exchange completed but displaced evidence is unreadable",
            details={
                "expected_sha256": expected_sha256,
                "published_sha256": published_sha,
                "source": str(source),
                "replacement_path": str(replacement),
                "displaced_path": str(displaced),
                "uncertain_paths": [str(source), str(replacement), str(displaced)],
            },
        ) from exc
    if displaced_sha != expected_sha256:
        raise EditorPathError(
            "SOURCE_EXCHANGE_CONFLICT",
            "source changed during atomic exchange; all versions retained",
            details={
                "expected_sha256": expected_sha256,
                "displaced_sha256": displaced_sha,
                "published_sha256": published_sha,
                "source": str(source),
                "replacement_path": str(replacement),
                "displaced_path": str(displaced),
                "uncertain_paths": [str(source), str(replacement), str(displaced)],
            },
        )
    try:
        _move_noreplace_unix(replacement, displaced)
    except OSError as exc:
        raise EditorPathError(
            "SOURCE_EXCHANGE_CONFLICT",
            f"exchange succeeded but displaced normalization failed: {exc}",
            details={
                "expected_sha256": expected_sha256,
                "displaced_sha256": displaced_sha,
                "published_sha256": published_sha,
                "source": str(source),
                "replacement_path": str(replacement),
                "displaced_path": str(displaced),
                "uncertain_paths": [str(source), str(replacement), str(displaced)],
            },
        ) from exc
    fsync_directory(source.parent)
    return ConditionalReplaceResult(
        published_sha256=published_sha,
        displaced_sha256=displaced_sha,
        displaced_path=displaced,
        source_mode=source_mode,
    )
