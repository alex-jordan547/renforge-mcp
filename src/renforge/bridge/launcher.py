"""Launch a Ren'Py project with the RenForge bridge injected.

Injects ``bridge.rpy`` into ``<project>/game/``, starts the game, waits for the
bridge to publish ``<project>/.renforge/bridge.json``, and returns a connected
:class:`~renforge.bridge.client.BridgeClient`. Closing the session force-kills
the game and removes the injected file.
"""

from __future__ import annotations

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


def remove_bridge_artifacts(project_root: Path) -> None:
    """Delete every file the bridge injects or leaves behind on ``project_root``.

    Safe to call more than once and whether or not the game is running; missing
    files are ignored. Shared by :meth:`BridgeSession.close` and the
    cross-process stop path so a session torn down from another process cleans
    up the same set of files.
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

    def __enter__(self) -> "BridgeSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self, timeout: float = 10.0) -> dict[str, Any]:
        """Stop the game, Xvfb group if any, and temporary session files."""
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
                    cleaned["renpy_process"] = True
                except (ProcessLookupError, PermissionError):
                    try:
                        self.process.kill()
                        cleaned["renpy_process"] = True
                    except Exception:
                        failed.append("renpy_process")
            else:
                try:
                    self.process.kill()
                    cleaned["renpy_process"] = True
                except Exception:
                    failed.append("renpy_process")
            try:
                self.process.wait(timeout=timeout)
            except Exception:
                failed.append("renpy_wait")
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
                    )
                except Exception:
                    pass  # bridge.json not fully written yet, retry
            time.sleep(0.3)
    except LaunchError:
        _terminate(process, headless)
        remove_bridge_artifacts(project.root)
        if cleanup_savedir and temporary_savedir is not None:
            shutil.rmtree(temporary_savedir, ignore_errors=True)
        raise
    except BaseException:
        _terminate(process, headless)
        remove_bridge_artifacts(project.root)
        if cleanup_savedir and temporary_savedir is not None:
            shutil.rmtree(temporary_savedir, ignore_errors=True)
        raise

    _terminate(process, headless)
    remove_bridge_artifacts(project.root)
    if cleanup_savedir and temporary_savedir is not None:
        shutil.rmtree(temporary_savedir, ignore_errors=True)
    raise LaunchError(
        "BRIDGE_CONNECTION_TIMEOUT",
        f"Bridge did not come up within {startup_timeout}s",
        phase="waiting_for_bridge",
        suggested_fix="Increase timeout, check the project launches manually, or inspect log.txt.",
        details={"phases": phases, "environment": capabilities.to_dict()},
    )


def _terminate(process: subprocess.Popen, headless: bool) -> None:
    """Terminate the launched process; in headless mode, its whole group.

    Under ``xvfb-run`` the tracked process is the wrapper — signalling it alone
    would orphan the game and the Xvfb server.
    """
    if headless:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError):
            pass
    process.terminate()


__all__ = ["BridgeSession", "launch_with_bridge", "remove_bridge_artifacts"]
