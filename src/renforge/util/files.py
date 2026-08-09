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
    flags = os.O_RDONLY
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
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    if follow_symlinks:
        destination = destination.resolve()
    elif destination.is_symlink():
        raise ValueError("atomic-write destination must not be a symlink: %s" % destination)

    fd, temp_name = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=str(destination.parent))
    try:
        written = 0
        with os.fdopen(fd, "wb") as handle:
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
        if not hasattr(os, "fchmod"):
            os.chmod(temp_name, mode)
        if not follow_symlinks and destination.is_symlink():
            raise ValueError("atomic-write destination must not be a symlink: %s" % destination)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


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
