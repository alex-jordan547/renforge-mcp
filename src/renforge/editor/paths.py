from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath


class EditorPathError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


def sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_RDONLY"):
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
