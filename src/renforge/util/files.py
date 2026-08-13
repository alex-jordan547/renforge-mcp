from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable, NoReturn, Union

DATA_ENCODING: str = "utf-8"
FILE_MODE: int = 0o644
_PRIVATE_DIR_MODE: int = 0o700
_PRIVATE_FILE_MODE: int = 0o600
_MODE_MASK: int = 0o777
_TEMP_ATTEMPTS: int = 32
_REPARSE_POINT: int = 0x400


class PrivatePathError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _open_regular_nofollow(path: Path) -> tuple[int, os.stat_result]:
    target = Path(path)
    before = target.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise OSError(errno.ELOOP, "path is not a regular non-symlink file", str(target))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(target), flags)
    try:
        opened = os.fstat(fd)
        after = target.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(after.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise OSError(errno.EAGAIN, "file changed while opening", str(target))
        return fd, opened
    except Exception:
        os.close(fd)
        raise


def hash_file_nofollow(path: Path) -> str:
    """Hash one stable regular-file descriptor without following the leaf."""
    fd, _opened = _open_regular_nofollow(path)
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def copy_regular_file_nofollow(
    source: Path,
    destination: Path,
    *,
    max_bytes: int,
) -> int:
    """Copy a regular source descriptor to a new file without following links."""
    source_fd, source_st = _open_regular_nofollow(source)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    destination_fd: int | None = None
    try:
        destination_fd = os.open(str(target), flags, stat.S_IMODE(source_st.st_mode))
        copied = 0
        while True:
            chunk = os.read(source_fd, min(1024 * 1024, max_bytes - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > max_bytes:
                raise OSError(errno.EFBIG, "file exceeds copy quota", str(source))
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, "could not write copied file", str(target))
                view = view[written:]
        os.fsync(destination_fd)
        return copied
    except Exception:
        if destination_fd is not None:
            os.close(destination_fd)
            destination_fd = None
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)


def write_exclusive_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(target), flags, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_atomic_chunks(
    path: str | os.PathLike[str],
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    follow_symlinks: bool,
    max_bytes: int | None = None,
) -> None:
    destination = Path(path).expanduser()
    if follow_symlinks:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = destination.resolve()
        _write_atomic_chunks_follow(destination, chunks, encoding=encoding, mode=mode, max_bytes=max_bytes)
        return
    _write_atomic_anchored(
        destination,
        chunks,
        encoding=encoding,
        mode=mode,
        max_bytes=max_bytes,
    )


def _write_atomic_chunks_follow(
    destination: Path,
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    max_bytes: int | None,
) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=str(destination.parent))
    try:
        owned = fd
        fd = -1
        _write_chunks_to_fd(owned, chunks, encoding=encoding, mode=mode, max_bytes=max_bytes, own_fd=True)
        if not hasattr(os, "fchmod"):
            os.chmod(temp_name, mode)
        os.replace(temp_name, destination)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(temp_name):
            os.remove(temp_name)


def _leaf_name(path: Path) -> str:
    name = path.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("destination name must be a basename: %s" % path)
    return name


def _write_chunks_to_fd(
    fd: int,
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    max_bytes: int | None,
    own_fd: bool,
) -> None:
    written = 0
    handle = os.fdopen(fd, "wb") if own_fd else os.fdopen(fd, "wb", closefd=False)
    try:
        for chunk in chunks:
            encoded = chunk.encode(encoding) if isinstance(chunk, str) else chunk
            written += len(encoded)
            if max_bytes is not None and written > max_bytes:
                raise ValueError("atomic-write payload exceeds %d bytes" % max_bytes)
            handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), mode)
    finally:
        handle.close()


def _symlink_error(path: Path | str, *, action: str) -> ValueError:
    return ValueError("%s destination must not be a symlink: %s" % (action, path))


def _is_loop_error(exc: OSError) -> bool:
    return exc.errno in {errno.ELOOP, getattr(errno, "EMLINK", errno.ELOOP)}


def _posix_dir_fd_supported() -> bool:
    supported = getattr(os, "supports_dir_fd", set())
    return os.open in supported and os.rename in supported and os.unlink in supported


def _open_directory_nofollow_posix(path: Path, *, action: str = "atomic-write") -> int:
    flags = _open_flags(os.O_RDONLY, getattr(os, "O_DIRECTORY", 0), getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        if _is_loop_error(exc) or path.is_symlink():
            raise _symlink_error(path, action=action) from exc
        raise
    try:
        opened = os.fstat(fd)
        after = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(after.st_mode)
            or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise _symlink_error(path, action=action)
        return fd
    except Exception:
        os.close(fd)
        raise


def _lstat_at_posix(dir_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except TypeError:
        try:
            return os.lstat(name, dir_fd=dir_fd)
        except FileNotFoundError:
            return None


def _reject_leaf_symlink_posix(dir_fd: int, name: str, destination: Path) -> None:
    st = _lstat_at_posix(dir_fd, name)
    if st is not None and stat.S_ISLNK(st.st_mode):
        raise _symlink_error(destination, action="atomic-write")


def _write_atomic_anchored_posix(
    destination: Path,
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    max_bytes: int | None,
) -> None:
    name = _leaf_name(destination)
    parent = destination.parent
    if not parent.is_symlink():
        parent.mkdir(parents=True, exist_ok=True)
    dir_fd = _open_directory_nofollow_posix(parent)
    temp_name: str | None = None
    fd = -1
    try:
        _reject_leaf_symlink_posix(dir_fd, name, destination)
        flags = _open_flags(
            os.O_WRONLY,
            os.O_CREAT,
            os.O_EXCL,
            getattr(os, "O_NOFOLLOW", 0),
            getattr(os, "O_BINARY", 0),
        )
        last_error: BaseException | None = None
        for _ in range(_TEMP_ATTEMPTS):
            candidate = ".%s.%s.tmp" % (name, secrets.token_hex(8))
            try:
                fd = os.open(candidate, flags, mode, dir_fd=dir_fd)
                temp_name = candidate
                break
            except FileExistsError as exc:
                last_error = exc
                continue
            except OSError as exc:
                if _is_loop_error(exc):
                    raise _symlink_error(parent / candidate, action="atomic-write") from exc
                raise
        else:
            raise OSError("failed to allocate a nofollow temporary file in %s" % parent) from last_error
        owned = fd
        fd = -1
        _write_chunks_to_fd(owned, chunks, encoding=encoding, mode=mode, max_bytes=max_bytes, own_fd=True)
        if not hasattr(os, "fchmod"):
            os.chmod(temp_name, mode, dir_fd=dir_fd)
        _reject_leaf_symlink_posix(dir_fd, name, destination)
        os.rename(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        temp_name = None
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
        os.close(dir_fd)


def _append_nofollow_posix(path: Path, data: bytes, *, mode: int) -> None:
    name = _leaf_name(path)
    dir_fd = _open_directory_nofollow_posix(path.parent, action="append")
    try:
        flags = _open_flags(
            os.O_WRONLY,
            os.O_CREAT,
            os.O_APPEND,
            getattr(os, "O_NOFOLLOW", 0),
            getattr(os, "O_BINARY", 0),
        )
        try:
            fd = os.open(name, flags, mode, dir_fd=dir_fd)
        except OSError as exc:
            if _is_loop_error(exc):
                raise _symlink_error(path, action="append") from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("append destination is not a regular file: %s" % path)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, "could not append", str(path))
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def _write_atomic_anchored(
    destination: Path,
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    max_bytes: int | None,
) -> None:
    if os.name == "nt":
        _write_atomic_anchored_windows(
            destination, chunks, encoding=encoding, mode=mode, max_bytes=max_bytes
        )
        return
    if not _posix_dir_fd_supported():
        raise OSError("anchored nofollow writes require dir_fd support")
    _write_atomic_anchored_posix(
        destination, chunks, encoding=encoding, mode=mode, max_bytes=max_bytes
    )


def append_nofollow(path: str | os.PathLike[str], data: bytes, *, mode: int = 0o600) -> None:
    """Append to a regular file through a nofollow directory descriptor."""
    target = Path(path).expanduser()
    if os.name == "nt":
        _append_nofollow_windows(target, data, mode=mode)
        return
    if not _posix_dir_fd_supported():
        raise OSError("anchored nofollow writes require dir_fd support")
    _append_nofollow_posix(target, data, mode=mode)


def write_atomic(
    path: str | os.PathLike[str],
    data: Union[str, bytes],
    *,
    encoding: str = DATA_ENCODING,
    mode: int = FILE_MODE,
    follow_symlinks: bool = True,
) -> None:
    _write_atomic_chunks(
        path,
        (data,),
        encoding=encoding,
        mode=mode,
        max_bytes=None,
        follow_symlinks=follow_symlinks,
    )


def write_json_atomic(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = DATA_ENCODING,
    mode: int = FILE_MODE,
    follow_symlinks: bool = True,
    max_bytes: int | None = None,
) -> None:
    chunks = json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(data)
    _write_atomic_chunks(
        path,
        chunks,
        encoding=encoding,
        mode=mode,
        follow_symlinks=follow_symlinks,
        max_bytes=max_bytes,
    )


def ensure_private_directory(path: Path) -> Path:
    """Create or validate a private directory. Never repairs unsafe paths."""
    target = Path(path).expanduser()
    if os.name == "nt":
        return _ensure_private_directory_windows(target)
    return _ensure_private_directory_posix(target)


def ensure_nofollow_directory(path: Path) -> Path:
    """Create a directory without traversing symlink/reparse-point components."""
    target = _reject_link_components(
        Path(path).expanduser(),
        code="PRIVATE_DIRECTORY_UNSAFE",
        include_leaf=True,
    )
    _materialize_ancestor_dirs(target / ".leaf", code="PRIVATE_DIRECTORY_UNSAFE")
    try:
        st = target.lstat()
    except FileNotFoundError:
        raise PrivatePathError(
            "PRIVATE_DIRECTORY_UNSAFE",
            "directory was not created: %s" % target,
        )
    if _component_is_link(target):
        raise PrivatePathError(
            "PRIVATE_DIRECTORY_UNSAFE",
            "directory must not be a symlink or reparse point: %s" % target,
        )
    if not stat.S_ISDIR(st.st_mode):
        raise PrivatePathError(
            "PRIVATE_DIRECTORY_UNSAFE",
            "path is not a directory: %s" % target,
        )
    return target


def read_regular_file_nofollow(path: Path, *, max_bytes: int) -> bytes:
    """Read a private regular file without following links."""
    if not isinstance(max_bytes, int) or max_bytes < 0:
        raise PrivatePathError("PRIVATE_FILE_UNSAFE", "max_bytes must be a non-negative integer")
    target = Path(path).expanduser()
    if os.name == "nt":
        return _read_regular_file_windows(target, max_bytes=max_bytes)
    return _read_regular_file_posix(target, max_bytes=max_bytes)


def atomic_write_private_json(path: Path, payload: Mapping[str, Any], *, max_bytes: int) -> None:
    """Atomically publish private JSON with exclusive no-follow temporaries."""
    if not isinstance(payload, Mapping):
        raise PrivatePathError("PRIVATE_FILE_UNSAFE", "payload must be a mapping")
    if not isinstance(max_bytes, int) or max_bytes < 0:
        raise PrivatePathError("PRIVATE_FILE_UNSAFE", "max_bytes must be a non-negative integer")
    target = Path(path).expanduser()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(DATA_ENCODING)
    if len(encoded) > max_bytes:
        raise PrivatePathError(
            "PRIVATE_FILE_UNSAFE",
            "private JSON payload exceeds %d bytes" % max_bytes,
        )
    if os.name == "nt":
        _atomic_write_private_bytes_windows(target, encoded)
    else:
        _atomic_write_private_bytes_posix(target, encoded)


def _private_error(code: str, message: str, cause: BaseException | None = None) -> NoReturn:
    err = PrivatePathError(code, message)
    if cause is not None:
        raise err from cause
    raise err


def _dir_unsafe(message: str, cause: BaseException | None = None) -> NoReturn:
    _private_error("PRIVATE_DIRECTORY_UNSAFE", message, cause)


def _file_unsafe(message: str, cause: BaseException | None = None) -> NoReturn:
    _private_error("PRIVATE_FILE_UNSAFE", message, cause)


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _open_flags(*bits: int) -> int:
    flags = 0
    for bit in bits:
        flags |= bit
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _absolute_path(path: Path) -> Path:
    target = Path(path).expanduser()
    return target if target.is_absolute() else (Path.cwd() / target)


def _path_components(path: Path) -> list[Path]:
    """Root-to-leaf components without resolving symlinks."""
    target = _absolute_path(path)
    current = Path(target.anchor)
    components = [current] if target.anchor else []
    for part in target.parts[1:]:
        current = current / part
        components.append(current)
    return components


def _component_is_link(path: Path) -> bool:
    if os.name == "nt":
        return path.is_symlink() or _win_is_reparse(path)
    return _is_symlink(path)


def _reject_link_components(path: Path, *, code: str, include_leaf: bool) -> Path:
    """Fail closed when any existing component is a symlink/reparse point.

    Callers should pass canonical absolute project paths so host aliases such as
    macOS ``/var`` → ``/private/var`` are already resolved. The project root is
    not required to be mode-private — only free of intermediate link components.
    """
    target = _absolute_path(path)
    components = _path_components(target)
    if not include_leaf and components:
        components = components[:-1]
    for component in components:
        try:
            if os.name == "nt":
                exists = component.exists() or component.is_symlink() or _win_is_reparse(component)
            else:
                component.lstat()
                exists = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            _private_error(code, "failed to inspect path component: %s" % component, exc)
        if not exists:
            continue
        if _component_is_link(component):
            _private_error(
                code,
                "path component must not be a symlink or reparse point: %s" % component,
            )
    return target


def _materialize_ancestor_dirs(path: Path, *, code: str) -> None:
    """Create missing non-leaf ancestors as real directories; never through links."""
    for component in _path_components(path)[:-1]:
        try:
            st = component.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(str(component))
            except FileExistsError:
                if _component_is_link(component):
                    _private_error(
                        code,
                        "path component must not be a symlink or reparse point: %s" % component,
                    )
                continue
            except OSError as exc:
                _private_error(code, "failed to create path component: %s" % component, exc)
            continue
        except OSError as exc:
            _private_error(code, "failed to inspect path component: %s" % component, exc)
        if _component_is_link(component):
            _private_error(
                code,
                "path component must not be a symlink or reparse point: %s" % component,
            )
        if not stat.S_ISDIR(st.st_mode):
            _private_error(code, "path component is not a directory: %s" % component)


def _lstat_or(path: Path, *, code: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError:
        _private_error(code, "path does not exist: %s" % path)
    except OSError as exc:
        _private_error(code, "failed to stat path: %s" % path, exc)


def _validate_posix_dir(path: Path) -> Path:
    st = _lstat_or(path, code="PRIVATE_DIRECTORY_UNSAFE")
    if stat.S_ISLNK(st.st_mode):
        _dir_unsafe("private directory must not be a symlink: %s" % path)
    if not stat.S_ISDIR(st.st_mode):
        _dir_unsafe("private path is not a directory: %s" % path)
    if st.st_uid != os.geteuid():
        _dir_unsafe("private directory is not owned by the current user: %s" % path)
    if (st.st_mode & _MODE_MASK) != _PRIVATE_DIR_MODE:
        _dir_unsafe("private directory mode must be 0700: %s" % path)
    return path


def _validate_posix_file_stat(st: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(st.st_mode):
        _file_unsafe("private path is not a regular file: %s" % path)
    if st.st_uid != os.geteuid():
        _file_unsafe("private file is not owned by the current user: %s" % path)
    if (st.st_mode & _MODE_MASK) != _PRIVATE_FILE_MODE:
        _file_unsafe("private file mode must be 0600: %s" % path)


def _validate_posix_fd(fd: int, path: Path, expected: os.stat_result | None = None) -> os.stat_result:
    try:
        st = os.fstat(fd)
    except OSError as exc:
        _file_unsafe("failed to fstat private file: %s" % path, exc)
    _validate_posix_file_stat(st, path)
    if expected is not None and (st.st_dev != expected.st_dev or st.st_ino != expected.st_ino):
        _file_unsafe("private file identity changed during open: %s" % path)
    return st


def _ensure_private_directory_posix(path: Path) -> Path:
    target = _reject_link_components(path, code="PRIVATE_DIRECTORY_UNSAFE", include_leaf=True)
    if _is_symlink(target) or target.exists():
        return _validate_posix_dir(target)

    _materialize_ancestor_dirs(target, code="PRIVATE_DIRECTORY_UNSAFE")
    try:
        os.mkdir(str(target), _PRIVATE_DIR_MODE)
    except FileExistsError:
        _reject_link_components(target, code="PRIVATE_DIRECTORY_UNSAFE", include_leaf=True)
        return _validate_posix_dir(target)
    except OSError as exc:
        _dir_unsafe("failed to create private directory: %s" % target, exc)

    validated = _validate_posix_dir(target)
    parent = target.parent
    fsync_directory(parent if parent != target else validated)
    return validated


def _read_regular_file_posix(path: Path, *, max_bytes: int) -> bytes:
    target = _reject_link_components(path, code="PRIVATE_FILE_UNSAFE", include_leaf=True)
    st = _lstat_or(target, code="PRIVATE_FILE_UNSAFE")
    _validate_posix_file_stat(st, target)
    if st.st_size > max_bytes:
        _file_unsafe("private file exceeds %d bytes: %s" % (max_bytes, target))

    flags = _open_flags(os.O_RDONLY, getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(str(target), flags)
    except OSError as exc:
        _file_unsafe("failed to open private file: %s" % target, exc)
    try:
        _validate_posix_fd(fd, target, expected=st)
        payload = os.read(fd, max_bytes + 1)
    except PrivatePathError:
        raise
    except OSError as exc:
        _file_unsafe("failed to read private file: %s" % target, exc)
    finally:
        os.close(fd)

    if len(payload) > max_bytes:
        _file_unsafe("private file exceeds %d bytes: %s" % (max_bytes, target))
    return payload


def _open_private_temp_posix(directory: Path, *, prefix: str) -> tuple[int, Path]:
    flags = _open_flags(os.O_WRONLY, os.O_CREAT, os.O_EXCL, getattr(os, "O_NOFOLLOW", 0))
    last_error: BaseException | None = None
    for _ in range(_TEMP_ATTEMPTS):
        temp_path = directory / ("%s.%s.tmp" % (prefix, secrets.token_hex(8)))
        try:
            fd = os.open(str(temp_path), flags, _PRIVATE_FILE_MODE)
        except FileExistsError as exc:
            last_error = exc
            continue
        except OSError as exc:
            _file_unsafe("failed to create private temporary file in %s" % directory, exc)
        try:
            _validate_posix_fd(fd, temp_path)
        except PrivatePathError:
            try:
                os.close(fd)
            finally:
                try:
                    os.unlink(str(temp_path))
                except OSError:
                    pass
            raise
        return fd, temp_path
    _file_unsafe("failed to allocate a private temporary file in %s" % directory, last_error)


def _require_private_parent(path: Path) -> Path:
    try:
        return ensure_private_directory(path.parent)
    except PrivatePathError as exc:
        if exc.code == "PRIVATE_DIRECTORY_UNSAFE":
            _file_unsafe(exc.message, exc)
        raise


def _require_existing_private_file_posix(path: Path) -> None:
    if not (path.exists() or _is_symlink(path)):
        return
    if _is_symlink(path):
        _file_unsafe("private file destination must not be a symlink: %s" % path)
    st = _lstat_or(path, code="PRIVATE_FILE_UNSAFE")
    _validate_posix_file_stat(st, path)


def _atomic_write_private_bytes_posix(path: Path, data: bytes) -> None:
    target = _reject_link_components(path, code="PRIVATE_FILE_UNSAFE", include_leaf=True)
    parent = _require_private_parent(target)
    _require_existing_private_file_posix(target)

    fd, temp_path = _open_private_temp_posix(parent, prefix=".%s" % target.name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            _validate_posix_fd(handle.fileno(), temp_path)
        if _is_symlink(target):
            _file_unsafe("private file destination must not be a symlink: %s" % target)
        os.replace(str(temp_path), str(target))
        fsync_directory(parent)
    except PrivatePathError:
        raise
    except OSError as exc:
        _file_unsafe("failed to publish private file: %s" % target, exc)
    finally:
        try:
            if temp_path.exists() or _is_symlink(temp_path):
                os.unlink(str(temp_path))
        except OSError:
            pass


# --- Windows: reject reparse points; protected current-user DACL ---


def _win_attrs(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    get_attrs = ctypes.windll.kernel32.GetFileAttributesW  # type: ignore[attr-defined]
    get_attrs.argtypes = [wintypes.LPCWSTR]
    get_attrs.restype = wintypes.DWORD
    value = int(get_attrs(str(path)))
    if value == 0xFFFFFFFF:
        err = ctypes.GetLastError()
        if err in {2, 3}:
            raise FileNotFoundError(str(path))
        raise OSError(err, "GetFileAttributesW failed for %s" % path)
    return value


def _win_is_reparse(path: Path) -> bool:
    try:
        st = os.stat(str(path), follow_symlinks=False)
        attrs = getattr(st, "st_file_attributes", None)
        if attrs is not None:
            return bool(attrs & _REPARSE_POINT)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _file_unsafe("failed to inspect path attributes: %s" % path, exc)
    try:
        return bool(_win_attrs(path) & _REPARSE_POINT)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _file_unsafe("failed to inspect path attributes: %s" % path, exc)


def _win_advapi_kernel() -> tuple[Any, Any]:
    """Load kernel32/advapi32 with WinDLL so GetLastError is reliable."""
    import ctypes

    # WinDLL (stdcall) + use_last_error is required: cdll/windll without
    # use_last_error can report stale errors after a failed security call.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    return kernel32, advapi32


def _win_bind_sid_apis(kernel32: Any, advapi32: Any) -> None:
    """Declare pointer-width argtypes for SID helpers.

    Without argtypes, ctypes defaults integer arguments to c_int (32-bit). A
    64-bit PSID from TOKEN_USER then raises ``OverflowError: int too long to
    convert`` on ConvertSidToStringSidW — the Windows CI failure mode.
    """
    import ctypes
    from ctypes import wintypes

    if getattr(advapi32, "_renforge_sid_bound", False):
        return

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL

    # PSID is a pointer: must be c_void_p (pointer-width), never c_int.
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    # SetFileSecurityW ignores PROTECTED_DACL_SECURITY_INFORMATION; use
    # SetNamedSecurityInfoW so SE_DACL_PROTECTED actually sticks on NTFS.
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL

    # Out-params are pointer-to-pointer (PSID*, PACL*, PSECURITY_DESCRIPTOR*).
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD

    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL

    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    advapi32._renforge_sid_bound = True  # type: ignore[attr-defined]


def _win_sid_to_string(advapi32: Any, kernel32: Any, sid_ptr: Any) -> str:
    """Convert a PSID pointer to SDDL form with pointer-safe ctypes."""
    import ctypes
    from ctypes import wintypes

    if not sid_ptr:
        raise OSError("null SID pointer")
    # Accept either a c_void_p or a raw int; always pass pointer-width.
    if not isinstance(sid_ptr, ctypes.c_void_p):
        sid_ptr = ctypes.c_void_p(int(sid_ptr))
    string_sid = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(string_sid)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
    try:
        value = string_sid.value
        if not value:
            raise OSError("ConvertSidToStringSidW returned an empty SID")
        return str(value)
    finally:
        # LocalFree the native buffer held by the LPWSTR out-param.
        if string_sid:
            kernel32.LocalFree(string_sid)


def _win_current_sid() -> str:
    import ctypes
    from ctypes import wintypes

    kernel32, advapi32 = _win_advapi_kernel()
    _win_bind_sid_apis(kernel32, advapi32)

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        0x0008,  # TOKEN_QUERY
        ctypes.byref(token),
    ):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        size = wintypes.DWORD(0)
        # First call sizes the buffer; ERROR_INSUFFICIENT_BUFFER (122) is expected.
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise OSError(ctypes.get_last_error(), "GetTokenInformation size query failed")
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, buf, size.value, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")

        class _SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        class _TOKEN_USER(ctypes.Structure):
            _fields_ = [("User", _SID_AND_ATTRIBUTES)]

        user = ctypes.cast(buf, ctypes.POINTER(_TOKEN_USER)).contents
        return _win_sid_to_string(advapi32, kernel32, user.User.Sid)
    finally:
        kernel32.CloseHandle(token)


def _win_set_protected_dacl(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32, advapi32 = _win_advapi_kernel()
    _win_bind_sid_apis(kernel32, advapi32)

    sddl = "D:P(A;;FA;;;%s)(A;;FA;;;SY)(A;;FA;;;BA)" % _win_current_sid()
    sd = ctypes.c_void_p()
    size = wintypes.ULONG()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(sd), ctypes.byref(size)
    ):
        raise OSError(
            ctypes.get_last_error(),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW failed",
        )
    try:
        dacl_present = wintypes.BOOL(0)
        dacl_defaulted = wintypes.BOOL(0)
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            sd,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            raise OSError(ctypes.get_last_error(), "GetSecurityDescriptorDacl failed")
        if not dacl_present or not dacl:
            raise OSError("security descriptor has no DACL")
        # SE_FILE_OBJECT=1; DACL_SECURITY_INFORMATION|PROTECTED_DACL_SECURITY_INFORMATION
        status = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            0x00000004 | 0x80000000,
            None,
            None,
            dacl,
            None,
        )
        if status != 0:
            raise OSError(status, "SetNamedSecurityInfoW failed for %s" % path)
    finally:
        if sd:
            kernel32.LocalFree(sd)


def _win_validate_protected_dacl(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32, advapi32 = _win_advapi_kernel()
    _win_bind_sid_apis(kernel32, advapi32)

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [("AceType", wintypes.BYTE), ("AceFlags", wintypes.BYTE), ("AceSize", wintypes.WORD)]

    sd = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,  # SE_FILE_OBJECT
        0x00000004,  # DACL_SECURITY_INFORMATION
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if status != 0:
        raise OSError(status, "GetNamedSecurityInfoW failed for %s" % path)
    try:
        if not dacl:
            raise OSError("missing DACL on %s" % path)
        control = wintypes.DWORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise OSError(ctypes.get_last_error(), "GetSecurityDescriptorControl failed")
        if not (control.value & 0x1000):  # SE_DACL_PROTECTED
            raise OSError("DACL is not protected on %s" % path)

        allowed = {_win_current_sid(), "S-1-5-18", "S-1-5-32-544"}
        protected = bool(control.value & 0x1000)  # SE_DACL_PROTECTED
        inherited_ace = False
        acl = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents
        for index in range(acl.AceCount):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise OSError(ctypes.get_last_error(), "GetAce failed")
            header = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
            if header.AceType != 0:  # ACCESS_ALLOWED_ACE_TYPE
                raise OSError("unexpected ACE type on %s" % path)
            # INHERITED_ACE — reject inherited trustees even when the SD bit is sticky.
            if header.AceFlags & 0x10:
                inherited_ace = True
            # ACCESS_ALLOWED_ACE: header (4) + Mask (4) + SidStart
            sid_ptr = ctypes.c_void_p(int(ace.value or 0) + 8)
            sid_text = _win_sid_to_string(advapi32, kernel32, sid_ptr)
            if sid_text not in allowed:
                raise OSError("unexpected trustee on private path %s" % path)
        # Prefer SE_DACL_PROTECTED. Some volumes keep an explicit-only DACL without
        # persisting that control bit; accept those when no ACE is inherited.
        if not protected and inherited_ace:
            raise OSError("DACL is not protected on %s" % path)
        if not protected and acl.AceCount == 0:
            raise OSError("DACL is not protected on %s" % path)
    finally:
        if sd:
            kernel32.LocalFree(sd)


def _ensure_private_directory_windows(path: Path) -> Path:
    target = _reject_link_components(path, code="PRIVATE_DIRECTORY_UNSAFE", include_leaf=True)
    exists = target.exists() or target.is_symlink() or _win_is_reparse(target)
    if exists:
        if target.is_symlink() or _win_is_reparse(target):
            _dir_unsafe("private directory must not be a reparse point: %s" % target)
        if not target.is_dir():
            _dir_unsafe("private path is not a directory: %s" % target)
        try:
            _win_validate_protected_dacl(target)
        except OSError as exc:
            _dir_unsafe("private directory DACL is unsafe: %s" % target, exc)
        return target

    _materialize_ancestor_dirs(target, code="PRIVATE_DIRECTORY_UNSAFE")
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError:
        return _ensure_private_directory_windows(target)
    except OSError as exc:
        _dir_unsafe("failed to create private directory: %s" % target, exc)
    try:
        _win_set_protected_dacl(target)
        _win_validate_protected_dacl(target)
    except OSError as exc:
        _dir_unsafe("failed to protect private directory: %s" % target, exc)
    return target


def _read_regular_file_windows(path: Path, *, max_bytes: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    target = _reject_link_components(path, code="PRIVATE_FILE_UNSAFE", include_leaf=True)
    if target.is_symlink() or _win_is_reparse(target):
        _file_unsafe("private file must not be a reparse point: %s" % target)
    if not target.is_file():
        _file_unsafe("private path is not a regular file: %s" % target)
    try:
        _win_validate_protected_dacl(target)
    except OSError as exc:
        _file_unsafe("private file DACL is unsafe: %s" % target, exc)

    kernel32, _advapi32 = _win_advapi_kernel()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    create_file.restype = wintypes.HANDLE
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    read_file.restype = wintypes.BOOL
    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = [wintypes.HANDLE]
    get_file_type.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(target),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x02000000,  # FILE_FLAG_SEQUENTIAL_SCAN | FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid or handle is None:
        _file_unsafe(
            "failed to open private file: %s" % target,
            OSError(ctypes.get_last_error(), "CreateFileW"),
        )
    try:
        if get_file_type(handle) != 1:  # FILE_TYPE_DISK
            _file_unsafe("private path is not a regular file: %s" % target)
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            size = min(65536, remaining)
            buf = ctypes.create_string_buffer(size)
            read = wintypes.DWORD(0)
            if not read_file(handle, buf, size, ctypes.byref(read), None):
                _file_unsafe(
                    "failed to read private file: %s" % target,
                    OSError(ctypes.get_last_error(), "ReadFile"),
                )
            if read.value == 0:
                break
            chunks.append(buf.raw[: read.value])
            remaining -= read.value
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            _file_unsafe("private file exceeds %d bytes: %s" % (max_bytes, target))
        return payload
    finally:
        close_handle(handle)


def _atomic_write_private_bytes_windows(path: Path, data: bytes) -> None:
    target = _reject_link_components(path, code="PRIVATE_FILE_UNSAFE", include_leaf=True)
    if target.is_symlink() or (target.exists() and _win_is_reparse(target)):
        _file_unsafe("private file destination must not be a reparse point: %s" % target)
    parent = _require_private_parent(target)
    if target.exists():
        if target.is_symlink() or _win_is_reparse(target) or not target.is_file():
            _file_unsafe("private file destination is unsafe: %s" % target)
        try:
            _win_validate_protected_dacl(target)
        except OSError as exc:
            _file_unsafe("private file destination DACL is unsafe: %s" % target, exc)

    last_error: BaseException | None = None
    for _ in range(_TEMP_ATTEMPTS):
        temp_path = parent / (".%s.%s.tmp" % (target.name, secrets.token_hex(8)))
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = os.open(str(temp_path), flags, _PRIVATE_FILE_MODE)
        except FileExistsError as exc:
            last_error = exc
            continue
        except OSError as exc:
            _file_unsafe("failed to create private temporary file in %s" % parent, exc)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _win_set_protected_dacl(temp_path)
            _win_validate_protected_dacl(temp_path)
            if target.exists() and (target.is_symlink() or _win_is_reparse(target)):
                _file_unsafe("private file destination must not be a reparse point: %s" % target)
            os.replace(str(temp_path), str(target))
            _win_set_protected_dacl(target)
            _win_validate_protected_dacl(target)
            return
        except PrivatePathError:
            raise
        except OSError as exc:
            _file_unsafe("failed to publish private file: %s" % target, exc)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
    _file_unsafe("failed to allocate a private temporary file in %s" % parent, last_error)


# --- Windows: directory-handle anchored create / rename / append ---

_WIN_GENERIC_READ = 0x80000000
_WIN_GENERIC_WRITE = 0x40000000
_WIN_DELETE = 0x00010000
_WIN_SYNCHRONIZE = 0x00100000
_WIN_FILE_APPEND_DATA = 0x0004
_WIN_FILE_LIST_DIRECTORY = 0x0001
_WIN_FILE_ADD_FILE = 0x0002
_WIN_FILE_TRAVERSE = 0x0020
_WIN_FILE_READ_ATTRIBUTES = 0x0080
_WIN_FILE_SHARE_ALL = 0x00000007
_WIN_OPEN_EXISTING = 3
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_ATTRIBUTE_NORMAL = 0x80
_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x10
_WIN_FILE_OPEN = 1
_WIN_FILE_CREATE = 2
_WIN_FILE_OPEN_IF = 3
_WIN_FILE_DIRECTORY_FILE = 0x00000001
_WIN_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_WIN_FILE_NON_DIRECTORY_FILE = 0x00000040
_WIN_FILE_OPEN_REPARSE_POINT = 0x00200000
_WIN_OBJ_CASE_INSENSITIVE = 0x00000040
_WIN_FILE_RENAME_INFO = 3
_WIN_INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF


def _win_ntdll() -> Any:
    import ctypes

    return ctypes.WinDLL("ntdll", use_last_error=True)  # type: ignore[attr-defined]


def _win_unicode_string(name: str) -> tuple[Any, Any]:
    import ctypes
    from ctypes import wintypes

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    buf = ctypes.create_unicode_buffer(name)
    us = UNICODE_STRING()
    us.Length = len(name) * 2
    us.MaximumLength = (len(name) + 1) * 2
    us.Buffer = ctypes.cast(buf, wintypes.LPWSTR)
    return us, buf


def _win_nt_create(
    *,
    root: int,
    name: str,
    access: int,
    disposition: int,
    options: int,
    share: int = _WIN_FILE_SHARE_ALL,
    attributes: int = _WIN_FILE_ATTRIBUTE_NORMAL,
) -> int:
    import ctypes
    from ctypes import wintypes

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.c_void_p),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p)]

    ntdll = _win_ntdll()
    ntdll.NtCreateFile.restype = ctypes.c_long
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    us, buf = _win_unicode_string(name)
    oa = OBJECT_ATTRIBUTES()
    oa.Length = ctypes.sizeof(OBJECT_ATTRIBUTES)
    oa.RootDirectory = root
    oa.ObjectName = ctypes.cast(ctypes.byref(us), ctypes.c_void_p)
    oa.Attributes = _WIN_OBJ_CASE_INSENSITIVE
    iosb = IO_STATUS_BLOCK()
    handle = wintypes.HANDLE()
    status = ntdll.NtCreateFile(
        ctypes.byref(handle),
        access,
        ctypes.byref(oa),
        ctypes.byref(iosb),
        None,
        attributes,
        share,
        disposition,
        options,
        None,
        0,
    )
    _ = buf
    if status < 0:
        raise OSError(status, "NtCreateFile failed for %s" % name)
    return int(handle.value or 0)


def _win_close(handle: int) -> None:
    if not handle:
        return
    kernel32, _advapi32 = _win_advapi_kernel()
    kernel32.CloseHandle.argtypes = [__import__("ctypes").wintypes.HANDLE]  # type: ignore[attr-defined]
    kernel32.CloseHandle.restype = __import__("ctypes").wintypes.BOOL  # type: ignore[attr-defined]
    kernel32.CloseHandle(handle)


def _win_handle_info(handle: int) -> Any:
    import ctypes
    from ctypes import wintypes

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32, _advapi32 = _win_advapi_kernel()
    info = BY_HANDLE_FILE_INFORMATION()
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    get_info.restype = wintypes.BOOL
    if not get_info(handle, ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
    return info


def _win_reject_reparse_handle(handle: int, path: Path, *, action: str) -> Any:
    info = _win_handle_info(handle)
    if info.dwFileAttributes & _REPARSE_POINT:
        raise _symlink_error(path, action=action)
    return info


def _open_directory_nofollow_windows(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32, _advapi32 = _win_advapi_kernel()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    create_file.restype = wintypes.HANDLE
    access = (
        _WIN_FILE_LIST_DIRECTORY
        | _WIN_FILE_ADD_FILE
        | _WIN_FILE_TRAVERSE
        | _WIN_FILE_READ_ATTRIBUTES
        | _WIN_SYNCHRONIZE
    )
    handle = create_file(
        str(path),
        access,
        _WIN_FILE_SHARE_ALL,
        None,
        _WIN_OPEN_EXISTING,
        _WIN_FILE_FLAG_BACKUP_SEMANTICS | _WIN_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid or handle is None:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed for %s" % path)
    try:
        info = _win_reject_reparse_handle(handle, path, action="atomic-write")
        if not (info.dwFileAttributes & _WIN_FILE_ATTRIBUTE_DIRECTORY):
            raise ValueError("path is not a directory: %s" % path)
        return int(handle)
    except Exception:
        _win_close(int(handle))
        raise


def _win_write_handle(handle: int, data: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32, _advapi32 = _win_advapi_kernel()
    write_file = kernel32.WriteFile
    write_file.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    write_file.restype = wintypes.BOOL
    flush = kernel32.FlushFileBuffers
    flush.argtypes = [wintypes.HANDLE]
    flush.restype = wintypes.BOOL
    view = memoryview(data)
    while view:
        written = wintypes.DWORD(0)
        chunk = bytes(view[:65536])
        buf = ctypes.create_string_buffer(chunk, len(chunk))
        if not write_file(handle, buf, len(chunk), ctypes.byref(written), None):
            raise OSError(ctypes.get_last_error(), "WriteFile failed")
        if written.value <= 0:
            raise OSError(errno.EIO, "could not write file")
        view = view[written.value :]
    if not flush(handle):
        raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")


def _win_rename_at(file_handle: int, dir_handle: int, name: str) -> None:
    import ctypes
    from ctypes import wintypes

    class FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(name) + 1)),
        ]

    kernel32, _advapi32 = _win_advapi_kernel()
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    info = FILE_RENAME_INFO()
    info.ReplaceIfExists = 1
    info.RootDirectory = dir_handle
    info.FileNameLength = len(name) * 2
    info.FileName = name
    if not set_info(file_handle, _WIN_FILE_RENAME_INFO, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle rename failed")


def _win_leaf_is_reparse(dir_handle: int, name: str, destination: Path) -> None:
    try:
        handle = _win_nt_create(
            root=dir_handle,
            name=name,
            access=_WIN_FILE_READ_ATTRIBUTES | _WIN_SYNCHRONIZE,
            disposition=_WIN_FILE_OPEN,
            options=_WIN_FILE_SYNCHRONOUS_IO_NONALERT
            | _WIN_FILE_OPEN_REPARSE_POINT
            | _WIN_FILE_NON_DIRECTORY_FILE,
        )
    except OSError:
        return
    try:
        _win_reject_reparse_handle(handle, destination, action="atomic-write")
    finally:
        _win_close(handle)


def _encode_chunks(chunks: Iterable[str | bytes], *, encoding: str, max_bytes: int | None) -> bytes:
    parts: list[bytes] = []
    written = 0
    for chunk in chunks:
        encoded = chunk.encode(encoding) if isinstance(chunk, str) else chunk
        written += len(encoded)
        if max_bytes is not None and written > max_bytes:
            raise ValueError("atomic-write payload exceeds %d bytes" % max_bytes)
        parts.append(encoded)
    return b"".join(parts)


def _write_atomic_anchored_windows(
    destination: Path,
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    max_bytes: int | None,
) -> None:
    del mode
    name = _leaf_name(destination)
    parent = destination.parent
    if not parent.is_symlink():
        parent.mkdir(parents=True, exist_ok=True)
    dir_handle = _open_directory_nofollow_windows(parent)
    temp_handle = 0
    temp_name: str | None = None
    try:
        _win_leaf_is_reparse(dir_handle, name, destination)
        payload = _encode_chunks(chunks, encoding=encoding, max_bytes=max_bytes)
        last_error: BaseException | None = None
        for _ in range(_TEMP_ATTEMPTS):
            candidate = ".%s.%s.tmp" % (name, secrets.token_hex(8))
            try:
                temp_handle = _win_nt_create(
                    root=dir_handle,
                    name=candidate,
                    access=_WIN_GENERIC_WRITE | _WIN_DELETE | _WIN_SYNCHRONIZE,
                    disposition=_WIN_FILE_CREATE,
                    options=_WIN_FILE_SYNCHRONOUS_IO_NONALERT
                    | _WIN_FILE_NON_DIRECTORY_FILE
                    | _WIN_FILE_OPEN_REPARSE_POINT,
                )
                temp_name = candidate
                break
            except OSError as exc:
                last_error = exc
                continue
        else:
            raise OSError("failed to allocate a nofollow temporary file in %s" % parent) from last_error
        _win_write_handle(temp_handle, payload)
        _win_leaf_is_reparse(dir_handle, name, destination)
        _win_rename_at(temp_handle, dir_handle, name)
        temp_name = None
    finally:
        if temp_handle:
            _win_close(temp_handle)
        if temp_name is not None:
            try:
                leftover = _win_nt_create(
                    root=dir_handle,
                    name=temp_name,
                    access=_WIN_DELETE | _WIN_SYNCHRONIZE,
                    disposition=_WIN_FILE_OPEN,
                    options=_WIN_FILE_SYNCHRONOUS_IO_NONALERT
                    | _WIN_FILE_NON_DIRECTORY_FILE
                    | _WIN_FILE_OPEN_REPARSE_POINT,
                )
            except OSError:
                leftover = 0
            if leftover:
                try:
                    import ctypes
                    from ctypes import wintypes

                    class FILE_DISPOSITION_INFO(ctypes.Structure):
                        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

                    kernel32, _advapi32 = _win_advapi_kernel()
                    info = FILE_DISPOSITION_INFO(True)
                    set_info = kernel32.SetFileInformationByHandle
                    set_info.argtypes = [
                        wintypes.HANDLE,
                        ctypes.c_int,
                        ctypes.c_void_p,
                        wintypes.DWORD,
                    ]
                    set_info.restype = wintypes.BOOL
                    set_info(leftover, 4, ctypes.byref(info), ctypes.sizeof(info))
                finally:
                    _win_close(leftover)
        _win_close(dir_handle)


def _append_nofollow_windows(path: Path, data: bytes, *, mode: int) -> None:
    del mode
    name = _leaf_name(path)
    dir_handle = _open_directory_nofollow_windows(path.parent)
    handle = 0
    try:
        handle = _win_nt_create(
            root=dir_handle,
            name=name,
            access=_WIN_FILE_APPEND_DATA | _WIN_FILE_READ_ATTRIBUTES | _WIN_SYNCHRONIZE,
            disposition=_WIN_FILE_OPEN_IF,
            options=_WIN_FILE_SYNCHRONOUS_IO_NONALERT
            | _WIN_FILE_NON_DIRECTORY_FILE
            | _WIN_FILE_OPEN_REPARSE_POINT,
        )
        _win_reject_reparse_handle(handle, path, action="append")
        info = _win_handle_info(handle)
        if info.dwFileAttributes & _WIN_FILE_ATTRIBUTE_DIRECTORY:
            raise OSError("append destination is not a regular file: %s" % path)
        _win_write_handle(handle, data)
    finally:
        if handle:
            _win_close(handle)
        _win_close(dir_handle)

