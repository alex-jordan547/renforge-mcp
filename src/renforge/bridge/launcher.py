"""Launch a Ren'Py project with the RenForge bridge injected.

Injects ``bridge.rpy`` into ``<project>/game/``, starts the game, waits for the
bridge to publish ``<project>/.renforge/bridge.json``, and returns a connected
:class:`~renforge.bridge.client.BridgeClient`. Closing the session force-kills
the game and removes the injected file.
"""

from __future__ import annotations

import errno
import json
import hashlib
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
from .client import BridgeClient
from ..editor import BridgeRuntimeProbe, EditorCoordinator, EditorEndpoint
from ..editor.paths import atomic_write_file

_BRIDGE_RESOURCE: Path = Path(__file__).parent / "bridge.rpy"
_INJECTED_NAME: str = "renforge_bridge.rpy"
_SESSION_INIT_NAME: str = "00renforge_session.rpy"
_EDITOR_RESOURCE: Path = Path(__file__).parent / "editor.rpy"
_EDITOR_ASSETS_RESOURCE: Path = Path(__file__).parent / "editor_assets"
_EDITOR_INJECTED_PREFIX: str = "zzrenforge_editor_"
_EDITOR_MANIFEST_NAME: str = "editor-session.json"
_EDITOR_DEFAULT_LANGUAGE: str = "en"
_EDITOR_SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh-CN")
# Languages Ren'Py's bundled font cannot draw: its own documentation says it
# omits Chinese, Japanese and Korean for size reasons.
_EDITOR_CJK_LANGUAGES: frozenset[str] = frozenset({"zh-CN"})


class ProjectBridgeLock:
    """A non-blocking, process-wide lock for one project's bridge artifacts."""

    def __init__(self, path: Path):
        self.path = path
        self._file: Any | None = None
        self.is_deferred = False

    def acquire(self) -> None:
        if self._file is not None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = self.path.open("a+b")
        except OSError as exc:
            raise LaunchError(
                "BRIDGE_PROJECT_LOCK_FAILED",
                f"Could not open the project bridge lock: {exc}",
                phase="acquiring_project_lock",
                suggested_fix="Check write permissions under .renforge/.",
            ) from exc
        try:
            self._lock_file(lock_file)
        except OSError as exc:
            try:
                lock_file.close()
            except OSError:
                pass
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise LaunchError(
                    "BRIDGE_PROJECT_LOCKED",
                    f"Another RenForge bridge session is active for {self.path.parent.parent}.",
                    phase="acquiring_project_lock",
                    suggested_fix="Stop the existing session before launching another for this project.",
                ) from exc
            raise LaunchError(
                "BRIDGE_PROJECT_LOCK_FAILED",
                f"Could not lock the project bridge: {exc}",
                phase="acquiring_project_lock",
                suggested_fix="Check write permissions under .renforge/.",
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

def _editor_manifest_path(project_root: Path) -> Path:
    return project_root / ".renforge" / _EDITOR_MANIFEST_NAME


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


def _write_editor_assets(assets_dir: Path) -> list[dict[str, str]]:
    """Copy the asset tree under ``assets_dir``, returning its manifest entries.

    ``O_EXCL`` on every file for the same reason the ``.rpy`` uses it: the game
    directory belongs to the user, and RenForge only ever removes bytes it can
    prove it wrote.
    """
    written: list[dict[str, str]] = []
    for relative, source in _editor_asset_sources():
        target = assets_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        written.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})
    return written


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


def _write_editor_font(assets_dir: Path, entries: list[dict[str, str]]) -> str:
    """Borrow a system CJK font when the interface language needs one.

    Nothing font-shaped ships in this repository. Ren'Py refuses to load a font
    from outside the game tree, so the only way to draw Chinese is to place one
    inside it — and that copy happens solely when a language actually calls for
    it, then leaves with the rest of the session artifacts.
    """
    if _editor_language() not in _EDITOR_CJK_LANGUAGES:
        return ""
    for candidate in _editor_font_candidates():
        try:
            if not candidate.is_file():
                continue
            payload = candidate.read_bytes()
        except OSError:
            continue
        relative = "fonts/cjk%s" % (candidate.suffix.lower() or ".ttf")
        target = assets_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        entries.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})
        return relative
    return ""


def _inject_editor_artifact(project: RenpyProject) -> tuple[Path, str, str]:
    """Inject the editor and its assets, returning ``(source path, assets dirname)``.

    The assets directory shares the ``.rpy`` stem, so the same collision-free
    draw covers both, and the runtime learns its name from the environment
    rather than guessing a hash it cannot see.
    """
    payload = _EDITOR_RESOURCE.read_bytes()
    for _attempt in range(32):
        stem = f"{_EDITOR_INJECTED_PREFIX}{secrets.token_hex(8)}"
        basename = f"{stem}.rpy"
        source_path = project.game_dir / basename
        assets_dir = project.game_dir / stem
        sibling_names = (basename, f"{basename}c", f"{basename}c.bak")
        sibling_paths = [project.game_dir / name for name in sibling_names]
        absent_before = {name: not path.exists() for name, path in zip(sibling_names, sibling_paths)}
        if not all(absent_before.values()):
            continue
        if any(path.is_symlink() for path in sibling_paths):
            continue
        # The assets directory is drawn from the same random stem, so it has to
        # clear the same absence bar before the draw is accepted.
        if assets_dir.exists() or assets_dir.is_symlink():
            continue
        try:
            descriptor = os.open(source_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            assets = _write_editor_assets(assets_dir)
            font_relative = _write_editor_font(assets_dir, assets)
            manifest = {
                "schema_version": 2,
                "basename": basename,
                "source_sha256": hashlib.sha256(payload).hexdigest(),
                "assets_dirname": stem,
                "assets": assets,
                "absent_before": {
                    "rpy": absent_before[basename],
                    "rpyc": absent_before[f"{basename}c"],
                    "rpyc_bak": absent_before[f"{basename}c.bak"],
                },
            }
            atomic_write_file(
                _editor_manifest_path(project.root),
                json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
            )
            return source_path, stem, font_relative
        except BaseException:
            shutil.rmtree(assets_dir, ignore_errors=True)
            try:
                source_path.unlink()
            except FileNotFoundError:
                pass
            raise
    raise LaunchError(
        "EDITOR_ARTIFACT_COLLISION",
        "Could not allocate a collision-free editor injection filename.",
        phase="injecting_editor",
    )


def _remove_editor_artifacts(project_root: Path) -> None:
    manifest_path = _editor_manifest_path(project_root)
    # Symlink check first: exists() follows symlinks and is False for a dangling
    # one, which would read a tampered manifest as absent and return, leaving the
    # symlink behind once the lock is released. Same invariant as the artifacts.
    if manifest_path.is_symlink():
        raise RuntimeError("editor artifact manifest became a symlink")
    if not manifest_path.exists():
        return
    if not manifest_path.is_file():
        raise RuntimeError("editor artifact manifest is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"editor artifact manifest is invalid: {exc}") from exc

    if not isinstance(manifest, dict):
        raise RuntimeError("editor artifact manifest is not a JSON object")

    basename = manifest.get("basename")
    expected_sha256 = manifest.get("source_sha256")
    absence = manifest.get("absent_before") if isinstance(manifest.get("absent_before"), dict) else {}
    if not isinstance(absence, dict):
        absence = {}

    if not isinstance(basename, str) or Path(basename).name != basename or not basename.startswith(
        _EDITOR_INJECTED_PREFIX
    ) or not basename.endswith(".rpy"):
        raise RuntimeError("editor artifact manifest failed ownership validation")

    if not isinstance(expected_sha256, str):
        raise RuntimeError("editor artifact manifest failed ownership validation")

    if len(expected_sha256) != 64:
        raise RuntimeError("editor artifact manifest failed ownership validation")

    try:
        int(expected_sha256, 16)
    except ValueError as exc:
        raise RuntimeError("editor artifact manifest failed ownership validation") from exc

    should_remove_compiled = {
        "rpyc": bool(absence.get("rpyc", False)),
        "rpyc_bak": bool(absence.get("rpyc_bak", False)),
    }

    source_path = project_root / "game" / basename
    # Every step below tolerates an already-absent artifact. Cleanup is retried
    # by BridgeSession.close() and the deferred reaper, so a partial failure must
    # never leave a state where the next attempt aborts on an artifact the
    # previous attempt already removed — that would strand the project lock
    # forever. Ownership is proven by the validated manifest and basename above,
    # plus the digest whenever the source is still present.
    #
    # Each symlink check runs *before* its exists() guard: exists() follows
    # symlinks and is False for a dangling one, so a tampered artifact would
    # otherwise read as absent, get skipped, and be left behind once the manifest
    # is gone. Tampering is the one condition that must stay fail-closed.
    if source_path.is_symlink():
        raise RuntimeError("editor source artifact became a symlink")
    if source_path.exists():
        if not source_path.is_file():
            raise RuntimeError("editor source artifact is not a regular file")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_sha256:
            raise RuntimeError("editor source artifact changed after injection")

    sibling_path = source_path.with_name(f"{basename}c")
    sibling_backup_path = source_path.with_name(f"{basename}c.bak")

    if should_remove_compiled["rpyc"]:
        if sibling_path.is_symlink():
            raise RuntimeError("editor compiled artifact became a symlink")
        if sibling_path.exists():
            if not sibling_path.is_file():
                raise RuntimeError("editor compiled artifact is not a regular file")
            sibling_path.unlink()

    if should_remove_compiled["rpyc_bak"]:
        if sibling_backup_path.is_symlink():
            raise RuntimeError("editor compiled backup artifact became a symlink")
        if sibling_backup_path.exists():
            if not sibling_backup_path.is_file():
                raise RuntimeError("editor compiled backup artifact is not a regular file")
            sibling_backup_path.unlink()

    _remove_editor_asset_tree(project_root / "game", manifest)

    source_path.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)


def _remove_editor_asset_tree(game_dir: Path, manifest: dict[str, Any]) -> None:
    """Remove the injected asset tree, file by proven file.

    Ownership is established exactly as it is for the ``.rpy``: the directory
    name must carry the injection prefix, and every file must still hash to what
    was written. A schema 1 manifest predates assets and owns nothing here.
    Directories are removed only once empty, so anything the user dropped inside
    survives — and keeps its parent alive with it.
    """
    dirname = manifest.get("assets_dirname")
    assets = manifest.get("assets")
    if dirname is None and not assets:
        return
    if (
        not isinstance(dirname, str)
        or not dirname
        or Path(dirname).name != dirname
        or not dirname.startswith(_EDITOR_INJECTED_PREFIX)
        or not isinstance(assets, list)
    ):
        raise RuntimeError("editor asset manifest failed ownership validation")

    root = game_dir / dirname
    if root.is_symlink():
        raise RuntimeError("editor asset directory became a symlink")
    if not root.exists():
        return
    if not root.is_dir():
        raise RuntimeError("editor asset directory is not a directory")

    for entry in assets:
        if not isinstance(entry, dict):
            raise RuntimeError("editor asset manifest failed ownership validation")
        relative = entry.get("path")
        expected_sha256 = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
        ):
            raise RuntimeError("editor asset manifest failed ownership validation")
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise RuntimeError("editor asset manifest failed ownership validation")
        try:
            int(expected_sha256, 16)
        except ValueError as exc:
            raise RuntimeError("editor asset manifest failed ownership validation") from exc

        target = root.joinpath(*parts)
        if target.is_symlink():
            raise RuntimeError("editor asset became a symlink")
        if not target.exists():
            continue
        if not target.is_file():
            raise RuntimeError("editor asset is not a regular file")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
            raise RuntimeError("editor asset changed after injection")
        target.unlink()

    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            path.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


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


def remove_bridge_artifacts(project_root: Path) -> None:
    """Delete every file the bridge injects or leaves behind on ``project_root``.

    The caller must hold the project's :class:`ProjectBridgeLock` unless this is
    a legacy maintenance or test cleanup. Missing files are ignored, so cleanup
    remains idempotent.
    """
    game_dir = project_root / "game"
    for path in (
        game_dir / _INJECTED_NAME,  # renforge_bridge.rpy
        game_dir / (_INJECTED_NAME + "c"),  # renforge_bridge.rpyc
        game_dir / (_INJECTED_NAME + "c.bak"),  # renforge_bridge.rpyc.bak
        game_dir / _SESSION_INIT_NAME,
        game_dir / (_SESSION_INIT_NAME + "c"),
        game_dir / (_SESSION_INIT_NAME + "c.bak"),
        project_root / ".renforge" / "bridge.json",
        project_root / "traceback.txt",
        project_root / "errors.txt",
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _remove_editor_artifacts(project_root)


def _write_session_init(project: RenpyProject, *, savedir: str | None) -> Path | None:
    """Inject an early init file that redirects the save directory when needed."""
    if not savedir:
        return None
    path = project.game_dir / _SESSION_INIT_NAME
    # init -1500 runs before most game options; env is the authority so the
    # same file works if a session is resumed with a different path.
    path.write_text(
        "\n".join(
            [
                "init -1500 python:",
                "    import os",
                "    _renforge_savedir = os.environ.get('RENFORGE_SAVEDIR')",
                "    if _renforge_savedir:",
                "        config.savedir = _renforge_savedir",
                "    _renforge_persistent = os.environ.get('RENFORGE_PERSISTENT_MODE')",
                "    if _renforge_persistent == 'empty':",
                "        # Keep persistent empty for isolated agent sessions.",
                "        try:",
                "            renpy.loadsave.location.unlink('persistent')",
                "        except Exception:",
                "            pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


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
            remove_bridge_artifacts(self._project_root)
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

    token = token or secrets.token_hex(16)
    _phase("injecting_bridge")
    try:
        injected = project.game_dir / _INJECTED_NAME
        injected.write_text(_BRIDGE_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
        _write_session_init(project, savedir=savedir_path)
        editor_assets_dirname: str | None = None
        editor_font_relative: str = ""
        if editor_endpoint is not None:
            _phase("injecting_editor")
            _, editor_assets_dirname, editor_font_relative = _inject_editor_artifact(project)
    except OSError as exc:
        raise LaunchError(
            "BRIDGE_FILE_NOT_CREATED",
            f"Could not inject the bridge into the project: {exc}",
            phase="injecting_bridge",
            suggested_fix="Check project write permissions under game/.",
        ) from exc

    env["RENFORGE_BRIDGE_TOKEN"] = token
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
            remove_bridge_artifacts(project.root)
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
        remove_bridge_artifacts(project.root)
        raise LaunchError(
            "RENPY_EXECUTABLE_NOT_FOUND",
            f"Could not start Ren'Py: {exc}",
            phase="starting_renpy",
            suggested_fix="Install a Ren'Py SDK via renforge or pass a valid version.",
        ) from exc
    except OSError as exc:
        remove_bridge_artifacts(project.root)
        raise LaunchError(
            "RENPY_PROCESS_EXITED",
            f"Failed to spawn Ren'Py: {exc}",
            phase="starting_renpy",
            suggested_fix="Check the SDK install and project path.",
        ) from exc

    phases.append({"phase": "starting_renpy", "pid": process.pid})
    info_path = project.root / ".renforge" / "bridge.json"
    deadline = time.time() + startup_timeout
    _phase("waiting_for_bridge", port=port or None)

    try:
        while time.time() < deadline:
            _raise_if_cancelled(cancel_event, phase="waiting_for_bridge")
            if process.poll() is not None:
                out = (process.stdout.read() if process.stdout else b"").decode("utf-8", "replace")
                err = (process.stderr.read() if process.stderr else b"").decode("utf-8", "replace")
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
            if info_path.exists():
                try:
                    manifest = json.loads(info_path.read_text(encoding="utf-8"))
                    if not isinstance(manifest, dict) or manifest.get("token") != token:
                        time.sleep(0.3)
                        continue
                    client = BridgeClient.from_project(project.root)
                    reply = client.ping()
                    if not isinstance(reply, dict) or reply.get("pong") is not True:
                        raise RuntimeError(f"bridge ping returned non-pong response: {reply}")
                    startup_ms = int((time.monotonic() - started) * 1000)
                    bridge_port = None
                    try:
                        bridge_port = int(getattr(getattr(client, "_config", None), "port", 0) or 0) or None
                    except Exception:
                        bridge_port = None
                    phases.append(
                        {
                            "phase": "ready",
                            "bridge_port": bridge_port,
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
                    pass  # bridge.json not fully written yet, retry
            time.sleep(0.3)
    except LaunchError:
        _teardown_failed_launch(
            process,
            headless,
            project.root,
            temporary_savedir if cleanup_savedir else None,
            project_lock,
        )
        raise
    except BaseException:
        _teardown_failed_launch(
            process,
            headless,
            project.root,
            temporary_savedir if cleanup_savedir else None,
            project_lock,
        )
        raise

    _teardown_failed_launch(
        process,
        headless,
        project.root,
        temporary_savedir if cleanup_savedir else None,
        project_lock,
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
    project_lock = ProjectBridgeLock(project.root / ".renforge" / "bridge.lock")
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
            remove_bridge_artifacts(project.root)
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
) -> None:
    if _terminate(process, headless):
        try:
            remove_bridge_artifacts(project_root)
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
                remove_bridge_artifacts(project_root)
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
