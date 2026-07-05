"""Launch a Ren'Py project with the RenForge bridge injected.

Injects ``bridge.rpy`` into ``<project>/game/``, starts the game, waits for the
bridge to publish ``<project>/.renforge/bridge.json``, and returns a connected
:class:`~renforge.bridge.client.BridgeClient`. Closing the session terminates
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


class BridgeSession:
    """A running game plus a connected bridge client. Use as a context manager."""

    def __init__(self, process: subprocess.Popen, client: BridgeClient, injected: Path, project_root: Path):
        self.process = process
        self.client = client
        self._injected = injected
        self._project_root = project_root

    def __enter__(self) -> "BridgeSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self, timeout: float = 10.0) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
        for path in (
            self._injected,
            self._injected.with_suffix(".rpyc"),
            self._project_root / ".renforge" / "bridge.json",
            self._project_root / "traceback.txt",
            self._project_root / "errors.txt",
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def launch_with_bridge(
    sdk: RenpySdk,
    project: RenpyProject,
    *,
    token: str | None = None,
    port: int = 0,
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

    command = project.renpy_command(sdk, ("run",))
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
                    return BridgeSession(process, client, injected, project.root)
                except Exception:
                    pass  # bridge.json not fully written yet, retry
            time.sleep(0.3)
    except BaseException:
        process.terminate()
        injected.unlink(missing_ok=True)
        raise

    process.terminate()
    injected.unlink(missing_ok=True)
    raise TimeoutError(f"Bridge did not come up within {startup_timeout}s")


__all__ = ["BridgeSession", "launch_with_bridge"]
