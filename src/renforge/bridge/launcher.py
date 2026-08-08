"""Launch a Ren'Py project with the RenForge bridge injected.

Injects ``bridge.rpy`` into ``<project>/game/``, starts the game, waits for the
bridge to publish ready metadata under ``<project>/.renforge/control/bridge.json``,
and returns a connected :class:`~renforge.bridge.client.BridgeClient`. Closing the
session force-kills the game and removes owned control metadata and injected files.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys  # retained for tests that patch renforge.bridge.launcher.sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..launch_env import (
    LaunchError,
    detect_environment,
    resolve_audio_strategy,
    resolve_display_strategy,
)
from ..project import RenpyProject
from ..sdk import RenpySdk
from .artifacts import (
    ArtifactOwnershipError,
    allocate_and_materialize,
    remove_owned_artifacts,
)
from .client import BridgeClient, BridgeConfig, BridgeProtocolError
from .control import read_bridge_info, write_starting_bridge_info
from ..editor import BridgeRuntimeProbe, EditorCoordinator, EditorEndpoint
from ..util.files import PrivatePathError, ensure_private_directory

_BRIDGE_RESOURCE: Path = Path(__file__).parent / "bridge.rpy"
_EDITOR_RESOURCE: Path = Path(__file__).parent / "editor.rpy"
_EDITOR_SCREENS_RESOURCE: Path = Path(__file__).parent / "screens"
_EDITOR_ASSETS_RESOURCE: Path = Path(__file__).parent / "editor_assets"
_EDITOR_DEFAULT_LANGUAGE: str = "en"
_EDITOR_SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh-CN")
# Languages Ren'Py's bundled font cannot draw: its own documentation says it
# omits Chinese, Japanese and Korean for size reasons.
_EDITOR_CJK_LANGUAGES: frozenset[str] = frozenset({"zh-CN"})

_BRIDGE_STARTUP_ERROR_PREFIX: str = "RENFORGE_BRIDGE_STARTUP_ERROR="
_BRIDGE_STARTUP_ERROR_CODES: frozenset[str] = frozenset(
    {
        "BRIDGE_MANIFEST_PUBLICATION_FAILED",
        "BRIDGE_INFO_CONFLICT",
        "BRIDGE_MANIFEST_IDENTITY_MISMATCH",
    }
)


class _LockPathUnsafe(Exception):
    """Lock path failed private-path validation; never repair."""


class ProjectBridgeLock:
    """A non-blocking, process-wide lock for one project's bridge artifacts.

    Owns ``<canonical-root>/.renforge/control/bridge.lock``. ``acquire()``
    validates/creates the private control directory before touching the lock.
    """

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.path = self.project_root / ".renforge" / "control" / "bridge.lock"
        self._file: Any | None = None
        self.is_deferred = False
        self.owned_session_id: str | None = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        try:
            ensure_private_directory(self.path.parent)
        except PrivatePathError as exc:
            raise LaunchError(
                "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
                exc.message,
                phase="preparing_control_directory",
                suggested_fix="Remove unsafe paths under .renforge/control and retry.",
            ) from exc

        try:
            lock_file = self._open_lock_file()
        except _LockPathUnsafe as exc:
            raise LaunchError(
                "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
                str(exc),
                phase="preparing_control_directory",
                suggested_fix="Remove unsafe paths under .renforge/control and retry.",
            ) from exc
        except OSError as exc:
            raise LaunchError(
                "BRIDGE_PROJECT_LOCK_FAILED",
                f"Could not open the project bridge lock: {exc}",
                phase="acquiring_project_lock",
                suggested_fix="Check write permissions under .renforge/control/.",
            ) from exc

        try:
            self._lock_file(lock_file)
        except OSError as exc:
            try:
                lock_file.close()
            except OSError:
                pass
            if self._is_lock_contention(exc):
                raise LaunchError(
                    "BRIDGE_PROJECT_LOCKED",
                    f"Another RenForge bridge session is active for {self.project_root}.",
                    phase="acquiring_project_lock",
                    suggested_fix=(
                        "Stop the existing session before launching another for this project."
                    ),
                ) from exc
            raise LaunchError(
                "BRIDGE_PROJECT_LOCK_FAILED",
                f"Could not lock the project bridge: {exc}",
                phase="acquiring_project_lock",
                suggested_fix="Check write permissions under .renforge/control/.",
            ) from exc
        self._file = lock_file

    def release(self) -> None:
        if self._file is None:
            return
        lock_file, self._file = self._file, None
        try:
            self._unlock_file(lock_file)
        finally:
            lock_file.close()

    def _open_lock_file(self) -> Any:
        if os.name == "nt":
            return self._open_lock_file_windows()
        return self._open_lock_file_posix()

    def _open_lock_file_posix(self) -> Any:
        import stat as stat_mod

        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            fd = os.open(str(self.path), flags, 0o600)
        except OSError as exc:
            if self._posix_open_is_unsafe(exc):
                raise _LockPathUnsafe(f"bridge lock path is unsafe: {self.path}") from exc
            raise

        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                raise _LockPathUnsafe(f"bridge lock is not a regular file: {self.path}")
            if st.st_uid != os.geteuid():
                raise _LockPathUnsafe(
                    f"bridge lock is not owned by the current user: {self.path}"
                )
            if (st.st_mode & 0o777) != 0o600:
                raise _LockPathUnsafe(f"bridge lock mode must be 0600: {self.path}")
            try:
                path_st = os.lstat(str(self.path))
            except OSError as exc:
                raise _LockPathUnsafe(f"bridge lock path is unsafe: {self.path}") from exc
            if stat_mod.S_ISLNK(path_st.st_mode):
                raise _LockPathUnsafe(f"bridge lock must not be a symlink: {self.path}")
            if path_st.st_dev != st.st_dev or path_st.st_ino != st.st_ino:
                raise _LockPathUnsafe(
                    f"bridge lock identity changed during open: {self.path}"
                )
            return os.fdopen(fd, "r+b", closefd=True)
        except _LockPathUnsafe:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            raise _LockPathUnsafe(f"bridge lock path is unsafe: {self.path}") from exc

    def _posix_open_is_unsafe(self, exc: OSError) -> bool:
        if exc.errno in {errno.ELOOP, getattr(errno, "EMLINK", -1)}:
            return True
        if exc.errno in {errno.EPERM, errno.EACCES} and self.path.is_symlink():
            return True
        message = str(exc).lower()
        if "symbolic link" in message or "symlink" in message:
            return True
        try:
            if self.path.is_symlink():
                return True
        except OSError:
            pass
        return False

    def _open_lock_file_windows(self) -> Any:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        from ..util import files as private_files

        if self.path.is_symlink() or (
            self.path.exists() and private_files._win_is_reparse(self.path)
        ):
            raise _LockPathUnsafe(f"bridge lock must not be a reparse point: {self.path}")

        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_ALWAYS = 4
        FILE_ATTRIBUTE_NORMAL = 0x00000080
        FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        FILE_TYPE_DISK = 0x0001
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        ERROR_ALREADY_EXISTS = 183

        create_file = ctypes.windll.kernel32.CreateFileW  # type: ignore[attr-defined]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(self.path),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            error = ctypes.GetLastError()
            raise OSError(error, f"CreateFileW failed for {self.path}")

        created_new = ctypes.GetLastError() != ERROR_ALREADY_EXISTS
        try:
            file_type = ctypes.windll.kernel32.GetFileType(handle)  # type: ignore[attr-defined]
            if file_type != FILE_TYPE_DISK:
                raise _LockPathUnsafe(f"bridge lock is not a regular file: {self.path}")

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

            info = BY_HANDLE_FILE_INFORMATION()
            if not ctypes.windll.kernel32.GetFileInformationByHandle(  # type: ignore[attr-defined]
                handle, ctypes.byref(info)
            ):
                raise OSError(
                    ctypes.GetLastError(),
                    f"GetFileInformationByHandle failed for {self.path}",
                )
            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            FILE_ATTRIBUTE_DIRECTORY = 0x10
            if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise _LockPathUnsafe(
                    f"bridge lock must not be a reparse point: {self.path}"
                )
            if info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
                raise _LockPathUnsafe(f"bridge lock is not a regular file: {self.path}")

            # CRT owns the HANDLE after open_osfhandle; do not CloseHandle it.
            fd = msvcrt.open_osfhandle(int(handle), os.O_RDWR)
            handle = None
            try:
                lock_file = os.fdopen(fd, "r+b", closefd=True)
            except BaseException:
                os.close(fd)
                raise
            try:
                if created_new:
                    private_files._win_set_protected_dacl(self.path)
                private_files._win_validate_protected_dacl(self.path)
            except BaseException as exc:
                try:
                    lock_file.close()
                except OSError:
                    pass
                if isinstance(exc, _LockPathUnsafe):
                    raise
                raise _LockPathUnsafe(
                    f"bridge lock DACL is unsafe: {self.path}"
                ) from exc
            return lock_file
        finally:
            if handle is not None:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]

    @staticmethod
    def _is_lock_contention(exc: OSError) -> bool:
        if exc.errno in {errno.EACCES, errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN)}:
            return True
        winerror = getattr(exc, "winerror", None)
        if winerror in {33, 32}:  # ERROR_LOCK_VIOLATION / ERROR_SHARING_VIOLATION
            return True
        return False

    @staticmethod
    def _lock_file(lock_file: Any) -> None:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_file(lock_file: Any) -> None:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)



_DEFERRED_LOCKS: set[ProjectBridgeLock] = set()

def _editor_screen_sources() -> list[Path]:
    """The region screens, in a stable order.

    One file per panel is what makes the UI work parallel: six authors touch six
    files instead of queueing on one. Ren'Py does not care how the source was
    organised, so they are concatenated at injection time.
    """
    if not _EDITOR_SCREENS_RESOURCE.is_dir():
        return []
    return sorted(_EDITOR_SCREENS_RESOURCE.glob("*.rpy"))


def _editor_payload() -> bytes:
    """The single artifact injected into the game, built from every source file.

    Keeping one injected file keeps one manifest entry, one digest, one cleanup
    path — the whole ownership contract stays as narrow as it was — while the
    sources stay split for the people writing them.
    """
    parts = [_EDITOR_RESOURCE.read_bytes()]
    for path in _editor_screen_sources():
        parts.append(b"\n\n" + path.read_bytes())
    return b"".join(parts)


def _editor_asset_sources() -> list[tuple[str, Path]]:
    """Every shipped editor asset, as ``(relative posix path, absolute source)``.

    The editor used to be a lone ``.rpy``. Rounded frames, icons and the locale
    catalogues cannot be expressed in screen language, so they travel beside it
    as real files. An absent or empty resource directory is normal and yields no
    assets, which keeps the whole asset path inert until something ships in it.
    """
    if not _EDITOR_ASSETS_RESOURCE.is_dir():
        return []
    return [
        (path.relative_to(_EDITOR_ASSETS_RESOURCE).as_posix(), path)
        for path in sorted(_EDITOR_ASSETS_RESOURCE.rglob("*"))
        if path.is_file()
    ]


def _editor_font_candidates() -> tuple[Path, ...]:
    """System fonts that cover Chinese, most likely first, by platform."""
    if sys.platform == "darwin":
        names = (
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Supplemental/Songti.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        )
    elif os.name == "nt":
        names = (
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        )
    else:
        names = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        )
    return tuple(Path(name) for name in names)


def _prepare_editor_asset_payloads() -> tuple[list[tuple[str, bytes]], str]:
    """Ship editor assets (and optional CJK font) as ``(relative path, bytes)``."""
    files: list[tuple[str, bytes]] = []
    for relative, source in _editor_asset_sources():
        files.append((relative, source.read_bytes()))
    font_relative = ""
    if _editor_language() in _EDITOR_CJK_LANGUAGES:
        for candidate in _editor_font_candidates():
            try:
                if not candidate.is_file():
                    continue
                payload = candidate.read_bytes()
            except OSError:
                continue
            font_relative = "fonts/cjk%s" % (candidate.suffix.lower() or ".ttf")
            files.append((font_relative, payload))
            break
    files.sort(key=lambda item: item[0])
    return files, font_relative


def _editor_language() -> str:
    """The editor's interface language, which follows RenForge — never the game.

    Changing the host game's language would rebuild its styles and replay its
    translation blocks, so the overlay carries its own catalogue instead.
    """
    requested = os.environ.get("RENFORGE_LANG", "").strip()
    return requested if requested in _EDITOR_SUPPORTED_LANGUAGES else _EDITOR_DEFAULT_LANGUAGE


def _editor_environment(
    endpoint: EditorEndpoint, *, assets_dirname: str, font_relative: str = ""
) -> dict[str, str]:
    language = _editor_language()
    # A language with no font to draw it renders as empty boxes. Readable
    # English is a better answer than an interface the user cannot read at all.
    if language in _EDITOR_CJK_LANGUAGES and not font_relative:
        language = _EDITOR_DEFAULT_LANGUAGE
    return {
        "RENFORGE_EDITOR_HOST": endpoint.host,
        "RENFORGE_EDITOR_PORT": str(endpoint.port),
        "RENFORGE_EDITOR_TOKEN": endpoint.token,
        "RENFORGE_EDITOR_PROTOCOL": str(endpoint.protocol_version),
        "RENFORGE_EDITOR_ASSETS": assets_dirname,
        "RENFORGE_EDITOR_LANG": language,
        "RENFORGE_EDITOR_FONT": font_relative,
    }


def remove_bridge_artifacts(
    project_root: Path,
    *,
    expected_session_id: str | None = None,
    remove_bridge_info: bool = True,
) -> bool:
    """Remove schema-3 owned session artifacts after full ownership validation.

    Returns ``False`` when no ownership manifest exists and ``True`` after a
    complete proven removal. Legacy fixed names, ``traceback.txt``,
    ``errors.txt``, and unowned pre-migration metadata are never touched.
    ``remove_bridge_info`` is retained for call-site compatibility; bridge-info
    cleanup is owned by the schema-3 manifest path when present.
    """
    _ = remove_bridge_info
    try:
        return remove_owned_artifacts(
            Path(project_root),
            expected_session_id=expected_session_id,
        )
    except ArtifactOwnershipError as exc:
        # Preserve fail-closed cleanup for BridgeSession retries.
        raise RuntimeError(exc.message) from exc


class BridgeSession:
    """A running game plus a connected bridge client. Use as a context manager."""

    def __init__(
        self,
        process: subprocess.Popen,
        client: BridgeClient,
        project_root: Path,
        headless: bool = False,
        *,
        display_mode: str = "native",
        temporary_savedir: Path | None = None,
        cleanup_savedir: bool = False,
        environment: dict[str, Any] | None = None,
        startup_ms: int | None = None,
        phases: list[dict[str, Any]] | None = None,
        project_lock: ProjectBridgeLock | None = None,
        editor_coordinator: EditorCoordinator | None = None,
    ):
        self.process = process
        self.client = client
        self.headless = headless
        self.display_mode = display_mode
        self.temporary_savedir = temporary_savedir
        self.cleanup_savedir = cleanup_savedir
        self.environment = environment or {}
        self.startup_ms = startup_ms
        self.phases = phases or []
        self.editor = editor_coordinator is not None
        self._project_root = project_root
        self._cleaned: dict[str, Any] = {}
        self._project_lock = project_lock
        self._editor_coordinator = editor_coordinator
        self._close_lock = threading.Lock()
        self._closed = False
        self._close_result: dict[str, Any] | None = None

    def __enter__(self) -> "BridgeSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        """Whether teardown completed and project ownership was released."""
        return self._closed

    def close(self, timeout: float = 10.0) -> dict[str, Any]:
        """Stop the game, Xvfb group if any, and temporary session files."""
        with self._close_lock:
            if self._closed:
                return self._close_result or {"cleaned": self._cleaned, "failed": ["close"]}
            self._close_result = self._close_resources(timeout)
            ownership_failures = {"process_alive", "bridge_artifacts", "temporary_savedir", "editor_coordinator"}
            if ownership_failures.intersection(self._close_result.get("failed", [])):
                return self._close_result
            if self._project_lock is not None:
                self._project_lock.release()
                self._project_lock = None
            self._closed = True
            return self._close_result

    def _close_resources(self, timeout: float) -> dict[str, Any]:
        cleaned: dict[str, Any] = {
            "renpy_process": False,
            "process_group": False,
            "bridge_artifacts": False,
            "temporary_savedir": False,
            "editor_coordinator": self._editor_coordinator is None,
        }
        failed: list[str] = []

        if self._editor_coordinator is not None:
            try:
                self._editor_coordinator.close(timeout=timeout)
                self._editor_coordinator = None
                cleaned["editor_coordinator"] = True
            except Exception:
                failed.append("editor_coordinator")

        if self.process.poll() is None:
            if self.headless or self.display_mode == "xvfb":
                # The tracked process is the xvfb-run wrapper: SIGKILL on it
                # alone would orphan the game and the Xvfb server, so kill the
                # whole process group (created via start_new_session).
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    cleaned["process_group"] = True
                except (ProcessLookupError, PermissionError):
                    try:
                        self.process.kill()
                    except Exception:
                        failed.append("renpy_process")
            else:
                try:
                    self.process.kill()
                except Exception:
                    failed.append("renpy_process")
            try:
                self.process.wait(timeout=timeout)
            except Exception:
                failed.append("renpy_wait")
            if self.process.poll() is None:
                failed.append("process_alive")
                self._cleaned = cleaned
                return {"cleaned": cleaned, "failed": failed}
            cleaned["renpy_process"] = True
        else:
            # Already exited: reap it so it does not linger as a zombie.
            try:
                self.process.wait()
                cleaned["renpy_process"] = True
            except Exception:
                pass

        try:
            expected = (
                self._project_lock.owned_session_id
                if self._project_lock is not None
                else None
            )
            remove_bridge_artifacts(self._project_root, expected_session_id=expected)
            cleaned["bridge_artifacts"] = True
        except Exception:
            failed.append("bridge_artifacts")

        if self.cleanup_savedir and self.temporary_savedir is not None:
            try:
                shutil.rmtree(self.temporary_savedir, ignore_errors=False)
                cleaned["temporary_savedir"] = True
            except FileNotFoundError:
                cleaned["temporary_savedir"] = True
            except Exception:
                failed.append("temporary_savedir")

        self._cleaned = cleaned
        result: dict[str, Any] = {"cleaned": cleaned}
        if failed:
            result["failed"] = failed
        return result


def _raise_if_cancelled(cancel_event: threading.Event | None, *, phase: str) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LaunchError(
            "LAUNCH_CANCELLED",
            "Launch was cancelled.",
            phase=phase,
        )


def _is_bridge_token(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _allocate_bridge_identity(*, token: str | None) -> tuple[str, str]:
    session_id = secrets.token_hex(16)
    if token is None:
        return session_id, secrets.token_hex(32)
    if not _is_bridge_token(token):
        raise LaunchError(
            "BRIDGE_TOKEN_INVALID",
            "Bridge token must be 64 lowercase hexadecimal characters.",
            phase="preparing_bridge_metadata",
            suggested_fix="Omit token to let RenForge generate one, or pass secrets.token_hex(32).",
        )
    return session_id, token


class _BoundedPipeReader:
    """Drain one child pipe into a bounded ring and watch for startup markers."""

    def __init__(
        self,
        stream: Any | None,
        *,
        watch_startup: bool = False,
        startup_event: threading.Event | None = None,
        startup_code: list[str | None] | None = None,
    ):
        self._stream = stream
        self._watch_startup = watch_startup
        self._startup_event = startup_event
        self._startup_code = startup_code
        self._chunks: list[bytes] = []
        self._size = 0
        self._line_buffer = bytearray()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        if stream is not None:
            self._thread = threading.Thread(
                target=self._run,
                name="renforge-bridge-pipe-reader",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            while True:
                if hasattr(stream, "read1"):
                    chunk = stream.read1(4096)
                elif hasattr(stream, "fileno"):
                    try:
                        chunk = os.read(stream.fileno(), 4096)
                    except (OSError, AttributeError):
                        chunk = stream.read(4096)
                else:
                    chunk = stream.read(4096)
                if not chunk:
                    break
                self._append(chunk)
                if self._watch_startup:
                    self._scan_startup_chunk(chunk)
        except Exception:
            pass

    def _append(self, chunk: bytes) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > 64 * 1024 and self._chunks:
                dropped = self._chunks.pop(0)
                self._size -= len(dropped)

    def _scan_startup_chunk(self, chunk: bytes) -> None:
        if self._startup_event is None or self._startup_event.is_set():
            return
        with self._lock:
            self._line_buffer.extend(chunk)
            if len(self._line_buffer) > 64 * 1024:
                self._line_buffer = self._line_buffer[-64 * 1024 :]
            while b"\n" in self._line_buffer:
                line_bytes, _, rest = self._line_buffer.partition(b"\n")
                self._line_buffer = bytearray(rest)
                if line_bytes.endswith(b"\r"):
                    line_bytes = line_bytes[:-1]
                line_str = line_bytes.decode("utf-8", "replace")
                if line_str.startswith(_BRIDGE_STARTUP_ERROR_PREFIX):
                    code = line_str[len(_BRIDGE_STARTUP_ERROR_PREFIX) :]
                    if code in _BRIDGE_STARTUP_ERROR_CODES:
                        if self._startup_code is not None:
                            self._startup_code[0] = code
                        self._startup_event.set()
                        return

    def tail(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    def join(self, timeout: float = 1.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)


def _raise_bridge_startup_error(code: str) -> None:
    raise LaunchError(
        code,
        "Bridge failed during startup publication.",
        phase="waiting_for_bridge",
        suggested_fix="Inspect the game log and retry the launch.",
    )


def _launch_after_project_lock(
    sdk: RenpySdk,
    project: RenpyProject,
    *,
    project_lock: ProjectBridgeLock,
    token: str | None = None,
    port: int = 0,
    warp: str | None = None,
    startup_timeout: float = 90.0,
    cancel_event: threading.Event | None = None,
    extra_env: dict[str, str] | None = None,
    display: str = "auto",
    audio: str = "auto",
    savedir: str | None = None,
    persistent: str = "existing",
    cleanup_on_stop: bool = True,
    preferences: str = "existing",
    editor_endpoint: EditorEndpoint | None = None,
    editor_coordinator: EditorCoordinator | None = None,
) -> BridgeSession:
    """Start ``project`` with the bridge and return a connected session.

    ``display`` / ``audio`` accept ``auto`` (recommended): detect the host
    capabilities, fall back to Xvfb and ``SDL_AUDIODRIVER=dummy`` when needed,
    and fail fast with a structured :class:`LaunchError` otherwise.

    ``savedir='temporary'`` isolates saves under a temp directory that is
    removed on session close when ``cleanup_on_stop`` is true.
    """
    started = time.monotonic()
    phases: list[dict[str, Any]] = []
    _raise_if_cancelled(cancel_event, phase="detecting_environment")

    def _phase(name: str, **extra: Any) -> None:
        record = {"phase": name, **extra}
        phases.append(record)

    _phase("detecting_environment")
    env = dict(os.environ)
    env.update(extra_env or {})
    capabilities = detect_environment(env)

    try:
        display_mode, display_env = resolve_display_strategy(display, capabilities)
        audio_env = resolve_audio_strategy(audio, capabilities)
    except LaunchError:
        raise
    env.update(display_env)
    env.update(audio_env)

    headless = display_mode == "xvfb"
    temporary_savedir: Path | None = None
    cleanup_savedir = False

    if savedir == "temporary":
        temporary_savedir = Path(tempfile.mkdtemp(prefix="renforge-saves-"))
        env["RENFORGE_SAVEDIR"] = str(temporary_savedir)
        cleanup_savedir = bool(cleanup_on_stop)
        savedir_path = str(temporary_savedir)
    elif savedir and savedir not in {"existing", "default"}:
        temporary_savedir = Path(savedir).expanduser().resolve()
        temporary_savedir.mkdir(parents=True, exist_ok=True)
        env["RENFORGE_SAVEDIR"] = str(temporary_savedir)
        cleanup_savedir = False
        savedir_path = str(temporary_savedir)
    else:
        savedir_path = None

    if persistent in {"empty", "existing", "copy", "fixture"}:
        env["RENFORGE_PERSISTENT_MODE"] = persistent
    elif persistent:
        env["RENFORGE_PERSISTENT_MODE"] = str(persistent)

    # preferences reserved for future fixture support; accepted for API stability.
    _ = preferences

    # Token may be caller-supplied; session id is allocated with the artifact
    # intent so names, ownership, and bridge.json share one identity.
    if token is not None and not _is_bridge_token(token):
        raise LaunchError(
            "BRIDGE_TOKEN_INVALID",
            "Bridge token must be 64 lowercase hexadecimal characters.",
            phase="preparing_bridge_metadata",
            suggested_fix="Omit token to let RenForge generate one, or pass secrets.token_hex(32).",
        )
    if token is None:
        token = secrets.token_hex(32)

    _phase("injecting_bridge")
    editor_assets_dirname: str | None = None
    editor_font_relative: str = ""
    try:
        editor_payload: bytes | None = None
        editor_assets: list[tuple[str, bytes]] | None = None
        if editor_endpoint is not None:
            _phase("injecting_editor")
            editor_payload = _editor_payload()
            editor_assets, editor_font_relative = _prepare_editor_asset_payloads()
        materialized = allocate_and_materialize(
            project,
            bridge_payload=_BRIDGE_RESOURCE.read_bytes(),
            include_session_init=bool(savedir_path),
            editor_payload=editor_payload,
            editor_asset_files=editor_assets,
            editor_font_relative=editor_font_relative,
        )
        session_id = materialized.session_id
        editor_assets_dirname = materialized.editor_assets_dirname
        if materialized.editor_font_relative:
            editor_font_relative = materialized.editor_font_relative
    except LaunchError:
        raise
    except OSError as exc:
        raise LaunchError(
            "BRIDGE_FILE_NOT_CREATED",
            f"Could not inject the bridge into the project: {exc}",
            phase="injecting_bridge",
            suggested_fix="Check project write permissions under game/.",
        ) from exc

    _phase("reserving_bridge_metadata")
    try:
        write_starting_bridge_info(
            project.root,
            session_id=session_id,
            token=token,
        )
    except LaunchError:
        # Do not cleanup against a failed reservation (unsafe preplanted metadata).
        raise
    except BridgeProtocolError as exc:
        raise LaunchError(
            "BRIDGE_INFO_CONFLICT",
            "Bridge metadata conflicted with the reserved launch identity.",
            phase="reserving_bridge_metadata",
            suggested_fix="Remove the conflicting .renforge/control/bridge.json and retry.",
        ) from exc
    except PrivatePathError as exc:
        raise LaunchError(
            "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
            "Bridge metadata path is unsafe and was left untouched.",
            phase="reserving_bridge_metadata",
            suggested_fix="Remove unsafe paths under .renforge/control and retry.",
        ) from exc
    except Exception as exc:
        raise LaunchError(
            "BRIDGE_MANIFEST_PUBLICATION_FAILED",
            f"Could not reserve starting bridge metadata: {exc}",
            phase="reserving_bridge_metadata",
            suggested_fix="Check write permissions under .renforge/control/.",
        ) from exc
    project_lock.owned_session_id = session_id

    env["RENFORGE_BRIDGE_TOKEN"] = token
    env["RENFORGE_BRIDGE_SESSION_ID"] = session_id
    env["RENFORGE_BRIDGE_PROJECT_ROOT"] = str(project.root)
    env["RENFORGE_BRIDGE_PORT"] = str(port)
    if editor_endpoint is not None:
        env.update(
            _editor_environment(
                editor_endpoint,
                assets_dirname=editor_assets_dirname or "",
                font_relative=editor_font_relative,
            )
        )

    command = project.renpy_command(sdk, ("run", "--warp", warp) if warp is not None else ("run",))
    if headless:
        if shutil.which("xvfb-run") is None:
            remove_bridge_artifacts(project.root, expected_session_id=session_id)
            raise LaunchError(
                "DISPLAY_START_FAILED",
                "Xvfb fallback selected but xvfb-run is not on PATH.",
                phase="starting_virtual_display",
                suggested_fix="Install xvfb or provide a DISPLAY.",
            )
        _phase("starting_virtual_display")
        command = ["xvfb-run", "-a", *command]

    _phase("starting_renpy")
    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=headless,
        )
    except FileNotFoundError as exc:
        remove_bridge_artifacts(project.root, expected_session_id=session_id)
        raise LaunchError(
            "RENPY_EXECUTABLE_NOT_FOUND",
            f"Could not start Ren'Py: {exc}",
            phase="starting_renpy",
            suggested_fix="Install a Ren'Py SDK via renforge or pass a valid version.",
        ) from exc
    except OSError as exc:
        remove_bridge_artifacts(project.root, expected_session_id=session_id)
        raise LaunchError(
            "RENPY_PROCESS_EXITED",
            f"Failed to spawn Ren'Py: {exc}",
            phase="starting_renpy",
            suggested_fix="Check the SDK install and project path.",
        ) from exc

    startup_event = threading.Event()
    startup_code: list[str | None] = [None]
    stdout_reader = _BoundedPipeReader(
        process.stdout,
        watch_startup=False,
    )
    stderr_reader = _BoundedPipeReader(
        process.stderr,
        watch_startup=True,
        startup_event=startup_event,
        startup_code=startup_code,
    )
    deadline = time.time() + startup_timeout
    _phase("waiting_for_bridge", port=port or None)

    try:
        while time.time() < deadline:
            _raise_if_cancelled(cancel_event, phase="waiting_for_bridge")
            if startup_event.is_set() and startup_code[0] is not None:
                _raise_bridge_startup_error(startup_code[0])
            if process.poll() is not None:
                stdout_reader.join(timeout=0.2)
                stderr_reader.join(timeout=0.2)
                if startup_event.is_set() and startup_code[0] is not None:
                    _raise_bridge_startup_error(startup_code[0])
                out = stdout_reader.tail().decode("utf-8", "replace")
                err = stderr_reader.tail().decode("utf-8", "replace")
                combined = (out + "\n" + err).lower()
                code = "RENPY_PROCESS_EXITED"
                suggested = "Inspect traceback.txt / errors.txt in the project root."
                if "audio" in combined and ("fail" in combined or "error" in combined):
                    code = "AUDIO_INITIALIZATION_FAILED"
                    suggested = "Relaunch with audio='dummy'."
                if "display" in combined or "x11" in combined or "wayland" in combined:
                    code = "DISPLAY_START_FAILED"
                    suggested = "Install xvfb or provide a working DISPLAY."
                raise LaunchError(
                    code,
                    f"Game exited (rc={process.returncode}) before the bridge came up.",
                    phase="starting_renpy",
                    suggested_fix=suggested,
                    details={"stdout": out[-4000:], "stderr": err[-4000:], "returncode": process.returncode},
                )
            try:
                info = read_bridge_info(
                    project.root,
                    require_ready=True,
                    expected_session_id=session_id,
                )
            except Exception:
                time.sleep(0.3)
                continue
            try:
                client = BridgeClient(
                    BridgeConfig(
                        host=info.host,
                        port=info.port,
                        token=info.token,
                    )
                )
                reply = client.ping()
                if not isinstance(reply, dict) or reply.get("pong") is not True:
                    raise RuntimeError(f"bridge ping returned non-pong response: {reply}")
                startup_ms = int((time.monotonic() - started) * 1000)
                phases.append(
                    {
                        "phase": "ready",
                        "bridge_port": info.port,
                        "startup_ms": startup_ms,
                    }
                )
                return BridgeSession(
                    process,
                    client,
                    project.root,
                    headless=headless,
                    display_mode=display_mode,
                    temporary_savedir=temporary_savedir,
                    cleanup_savedir=cleanup_savedir,
                    environment=capabilities.to_dict(),
                    startup_ms=startup_ms,
                    phases=phases,
                    project_lock=project_lock,
                    editor_coordinator=editor_coordinator,
                )
            except Exception:
                time.sleep(0.3)
                continue
    except LaunchError:
        _teardown_failed_launch(
            process,
            headless,
            project.root,
            temporary_savedir if cleanup_savedir else None,
            project_lock,
            expected_session_id=session_id,
        )
        raise
    except BaseException:
        _teardown_failed_launch(
            process,
            headless,
            project.root,
            temporary_savedir if cleanup_savedir else None,
            project_lock,
            expected_session_id=session_id,
        )
        raise

    _teardown_failed_launch(
        process,
        headless,
        project.root,
        temporary_savedir if cleanup_savedir else None,
        project_lock,
        expected_session_id=session_id,
    )
    raise LaunchError(
        "BRIDGE_CONNECTION_TIMEOUT",
        f"Bridge did not come up within {startup_timeout}s",
        phase="waiting_for_bridge",
        suggested_fix="Increase timeout, check the project launches manually, or inspect log.txt.",
        details={"phases": phases, "environment": capabilities.to_dict()},
    )



def launch_with_bridge(
    sdk: RenpySdk,
    project: RenpyProject,
    *,
    token: str | None = None,
    port: int = 0,
    warp: str | None = None,
    startup_timeout: float = 90.0,
    cancel_event: threading.Event | None = None,
    extra_env: dict[str, str] | None = None,
    display: str = "auto",
    audio: str = "auto",
    savedir: str | None = None,
    persistent: str = "existing",
    cleanup_on_stop: bool = True,
    preferences: str = "existing",
    editor: bool = False,
) -> BridgeSession:
    """Launch a bridge while exclusively owning this project's artifacts."""
    project_lock = ProjectBridgeLock(project.root)
    project_lock.acquire()
    editor_coordinator: EditorCoordinator | None = None
    try:
        remove_bridge_artifacts(project.root)
        editor_endpoint: EditorEndpoint | None = None
        if editor:
            editor_coordinator = EditorCoordinator(project, sdk)
            editor_coordinator.attach_runtime_probe(BridgeRuntimeProbe(project.root))
            editor_endpoint = editor_coordinator.start()
        session = _launch_after_project_lock(
            sdk,
            project,
            project_lock=project_lock,
            token=token,
            port=port,
            warp=warp,
            startup_timeout=startup_timeout,
            cancel_event=cancel_event,
            extra_env=extra_env,
            display=display,
            audio=audio,
            savedir=savedir,
            persistent=persistent,
            cleanup_on_stop=cleanup_on_stop,
            preferences=preferences,
            editor_endpoint=editor_endpoint,
            editor_coordinator=editor_coordinator,
        )
        return session
    except BaseException:
        if editor_coordinator is not None:
            try:
                editor_coordinator.close()
            except Exception:
                pass
        if project_lock.is_deferred:
            raise
        try:
            if project_lock.owned_session_id is not None:
                remove_bridge_artifacts(
                    project.root,
                    expected_session_id=project_lock.owned_session_id,
                )
            else:
                # Reservation never succeeded: never touch control/bridge.json.
                remove_bridge_artifacts(project.root, remove_bridge_info=False)
        finally:
            project_lock.release()
        raise


def _terminate(process: subprocess.Popen, headless: bool, timeout: float = 1.0) -> bool:
    """Stop a process with bounded TERM/KILL escalation and confirm its death.

    Under ``xvfb-run`` the tracked process is the wrapper — signalling it alone
    would orphan the game and the Xvfb server.
    """
    if process.poll() is not None:
        try:
            process.wait(timeout=0)
        except Exception:
            pass
        return process.poll() is not None

    _signal_process(process, headless, force=False)
    try:
        process.wait(timeout=timeout)
    except Exception:
        pass
    if process.poll() is not None:
        return True

    _signal_process(process, headless, force=True)
    try:
        process.wait(timeout=timeout)
    except Exception:
        pass
    return process.poll() is not None


def _signal_process(process: subprocess.Popen, headless: bool, *, force: bool) -> None:
    if headless:
        try:
            signal_number = signal.SIGKILL if force else signal.SIGTERM
            os.killpg(os.getpgid(process.pid), signal_number)
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill() if force else process.terminate()
    except Exception:
        pass


def _teardown_failed_launch(
    process: subprocess.Popen,
    headless: bool,
    project_root: Path,
    temporary_savedir: Path | None,
    project_lock: ProjectBridgeLock,
    *,
    expected_session_id: str | None = None,
) -> None:
    session_id = expected_session_id or project_lock.owned_session_id
    if _terminate(process, headless):
        try:
            remove_bridge_artifacts(project_root, expected_session_id=session_id)
            if temporary_savedir is not None:
                shutil.rmtree(temporary_savedir, ignore_errors=False)
            return
        except FileNotFoundError:
            return
        except Exception:
            pass

    project_lock.is_deferred = True
    _DEFERRED_LOCKS.add(project_lock)

    def reap() -> None:
        while process.poll() is None:
            try:
                process.wait(timeout=1.0)
            except Exception:
                time.sleep(0.1)
        while True:
            try:
                remove_bridge_artifacts(project_root, expected_session_id=session_id)
                if temporary_savedir is not None:
                    shutil.rmtree(temporary_savedir, ignore_errors=False)
                project_lock.release()
                _DEFERRED_LOCKS.discard(project_lock)
                return
            except FileNotFoundError:
                project_lock.release()
                _DEFERRED_LOCKS.discard(project_lock)
                return
            except Exception:
                time.sleep(0.1)

    threading.Thread(target=reap, name="renforge-bridge-reaper", daemon=True).start()


__all__ = [
    "BridgeSession",
    "ProjectBridgeLock",
    "launch_with_bridge",
    "remove_bridge_artifacts",
]
