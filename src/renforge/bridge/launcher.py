"""Launch a Ren'Py project with the RenForge bridge injected.

Injects ``bridge.rpy`` into ``<project>/game/``, starts the game, waits for the
bridge to publish ``<project>/.renforge/bridge.json``, and returns a connected
:class:`~renforge.bridge.client.BridgeClient`. Closing the session force-kills
the game and removes the injected file.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from pathlib import Path

from ..project import RenpyProject
from ..sdk import RenpySdk
from .client import BridgeClient

_BRIDGE_RESOURCE: Path = Path(__file__).parent / "bridge.rpy"
_INJECTED_NAME: str = "renforge_bridge.rpy"


def remove_bridge_artifacts(project_root: Path) -> None:
    """Delete every file the bridge injects or leaves behind on ``project_root``.

    Safe to call more than once and whether or not the game is running; missing
    files are ignored. Shared by :meth:`BridgeSession.close` and the
    cross-process stop path so a session torn down from another process cleans
    up the same set of files.
    """
    game_dir = project_root / "game"
    for path in (
        game_dir / _INJECTED_NAME,          # renforge_bridge.rpy
        game_dir / (_INJECTED_NAME + "c"),  # renforge_bridge.rpyc
        game_dir / (_INJECTED_NAME + "c.bak"),  # renforge_bridge.rpyc.bak
        project_root / ".renforge" / "bridge.json",
        project_root / "traceback.txt",
        project_root / "errors.txt",
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class BridgeSession:
    """A running game plus a connected bridge client. Use as a context manager."""

    def __init__(self, process: subprocess.Popen, client: BridgeClient, project_root: Path):
        self.process = process
        self.client = client
        self._project_root = project_root

    def __enter__(self) -> "BridgeSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self, timeout: float = 10.0) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=timeout)
        else:
            # Already exited: reap it so it does not linger as a zombie.
            self.process.wait()
        remove_bridge_artifacts(self._project_root)


def launch_with_bridge(
    sdk: RenpySdk,
    project: RenpyProject,
    *,
    token: str | None = None,
    port: int = 0,
    warp: str | None = None,
    startup_timeout: float = 60.0,
    extra_env: dict[str, str] | None = None,
) -> BridgeSession:
    """Start ``project`` with the bridge and return a connected session.

    Requires a display (Ren'Py's ``run`` opens a window); under WSLg this works
    out of the box, and headless CI should wrap the call with ``xvfb-run``.
    """
    token = token or secrets.token_hex(16)
    injected = project.game_dir / _INJECTED_NAME
    injected.write_text(_BRIDGE_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")

    env = dict(os.environ)
    env.update(extra_env or {})
    env["RENFORGE_BRIDGE_TOKEN"] = token
    env["RENFORGE_BRIDGE_PORT"] = str(port)

    command = project.renpy_command(sdk, ("run", "--warp", warp) if warp is not None else ("run",))
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    info_path = project.root / ".renforge" / "bridge.json"
    deadline = time.time() + startup_timeout
    try:
        while time.time() < deadline:
            if process.poll() is not None:
                out = (process.stdout.read() if process.stdout else b"").decode("utf-8", "replace")
                err = (process.stderr.read() if process.stderr else b"").decode("utf-8", "replace")
                raise RuntimeError(
                    f"Game exited (rc={process.returncode}) before the bridge came up.\n"
                    f"stdout:\n{out}\nstderr:\n{err}"
                )
            if info_path.exists():
                try:
                    client = BridgeClient.from_project(project.root)
                    client.ping()
                    return BridgeSession(process, client, project.root)
                except Exception:
                    pass  # bridge.json not fully written yet, retry
            time.sleep(0.3)
    except BaseException:
        process.terminate()
        remove_bridge_artifacts(project.root)
        raise

    process.terminate()
    remove_bridge_artifacts(project.root)
    raise TimeoutError(f"Bridge did not come up within {startup_timeout}s")


__all__ = ["BridgeSession", "launch_with_bridge", "remove_bridge_artifacts"]
