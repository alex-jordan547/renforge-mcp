"""Launch a Ren'Py project with the RenForge bridge injected.

Injects ``bridge.rpy`` into ``<project>/game/``, starts the game, waits for the
bridge to publish ``<project>/.renforge/bridge.json``, and returns a connected
:class:`~renforge.bridge.client.BridgeClient`. Closing the session force-kills
the game and removes the injected file.
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
from .client import BridgeClient

_BRIDGE_RESOURCE: Path = Path(__file__).parent / "bridge.rpy"
_INJECTED_NAME: str = "renforge_bridge.rpy"
_SESSION_INIT_NAME: str = "00renforge_session.rpy"


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
        self._project_root = project_root
        self._cleaned: dict[str, Any] = {}
        self._project_lock = project_lock
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
            ownership_failures = {"process_alive", "bridge_artifacts", "temporary_savedir"}
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
        }
        failed: list[str] = []

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
    except OSError as exc:
        raise LaunchError(
            "BRIDGE_FILE_NOT_CREATED",
            f"Could not inject the bridge into the project: {exc}",
            phase="injecting_bridge",
            suggested_fix="Check project write permissions under game/.",
        ) from exc

    env["RENFORGE_BRIDGE_TOKEN"] = token
    env["RENFORGE_BRIDGE_PORT"] = str(port)

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
) -> BridgeSession:
    """Launch a bridge while exclusively owning this project's artifacts."""
    project_lock = ProjectBridgeLock(project.root / ".renforge" / "bridge.lock")
    project_lock.acquire()
    try:
        remove_bridge_artifacts(project.root)
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
        )
        return session
    except BaseException:
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
