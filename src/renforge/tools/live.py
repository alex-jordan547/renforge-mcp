"""Live-game tools: drive a running Ren'Py project through the bridge.

These back the MCP tools that let an agent launch a game, look at it
(screenshots, state), advance dialogue, and pick menu choices. A module-level
registry keeps launched sessions alive across stateless tool calls; per-command
tools connect through ``<project>/.renforge/bridge.json`` so they also work
against a game launched elsewhere.

This module stays backend-agnostic (no MCP import); the server wraps the raw
PNG from :func:`screenshot_png` into an MCP image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..autopilot import autopilot as _autopilot
from ..bridge.client import BridgeClient, BridgeError
from ..bridge.launcher import BridgeSession, launch_with_bridge
from ..project import RenpyProject
from ..sdk import get_or_install_sdk

_SESSIONS: dict[str, BridgeSession] = {}


def _key(project_path: str | Path) -> str:
    return str(Path(project_path).expanduser().resolve())


def _client(project_path: str | Path) -> BridgeClient:
    project = RenpyProject(Path(project_path))
    return BridgeClient.from_project(project.root)


def _with_client(project_path: str | Path, fn: Callable[[BridgeClient], dict]) -> dict:
    try:
        client = _client(project_path)
    except FileNotFoundError:
        return {"ok": False, "error": "no running game (bridge not found); call renforge_launch first"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        return fn(client)
    except BridgeError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def launch_game(project_path: str, version: str = "stable", warp: str | None = None) -> dict:
    """Launch the project with the bridge injected, or reuse a live session."""
    try:
        project = RenpyProject(Path(project_path))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    key = _key(project.root)
    existing = _SESSIONS.get(key)
    if existing is not None and existing.process.poll() is None:
        try:
            if warp is None:
                state = existing.client.get_state()
                return {"ok": True, "already_running": True, "current_label": state.get("current_label")}
            existing.close()
        except Exception:
            existing.close()
        _SESSIONS.pop(key, None)

    try:
        sdk = get_or_install_sdk(version)
        launch_kwargs: dict[str, object] = {}
        if warp is not None:
            launch_kwargs["warp"] = warp
        session = launch_with_bridge(sdk, project, **launch_kwargs)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    _SESSIONS[key] = session
    label = None
    try:
        label = session.client.get_state().get("current_label")
    except Exception:
        pass
    return {"ok": True, "already_running": False, "current_label": label}


def stop_game(project_path: str) -> dict:
    session = _SESSIONS.pop(_key(project_path), None)
    if session is None:
        return {"ok": True, "was_running": False}
    session.close()
    return {"ok": True, "was_running": True}


def stop_all() -> None:
    for session in list(_SESSIONS.values()):
        try:
            session.close()
        except Exception:
            pass
    _SESSIONS.clear()


def game_state(project_path: str) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, **c.get_state()})


def advance(project_path: str) -> dict:
    return _with_client(project_path, lambda c: c.advance())


def list_choices(project_path: str) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, "choices": c.list_choices()})


def select_choice(project_path: str, text: str | None = None, index: int | None = None) -> dict:
    return _with_client(project_path, lambda c: c.select_choice(text=text, index=index))


def eval_expr(project_path: str, expr: str) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, "value": c.eval_expr(expr)})


def get_var(project_path: str, name: str) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, "value": c.get_var(name)})


def set_var(project_path: str, name: str, value: Any) -> dict:
    return _with_client(project_path, lambda c: c.set_var(name, value))


def poll_events(project_path: str, since: int = 0) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, **c.poll_events(since)})


def screenshot_png(project_path: str, width: int = 0, height: int = 0) -> bytes:
    """Return the current frame as PNG bytes (raises if no game is running)."""
    return _client(project_path).screenshot(width, height)


def run_autopilot(project_path: str, version: str = "stable", max_runs: int = 16, max_steps: int = 60) -> dict:
    """Explore the game's branches and return a coverage/crash report."""
    try:
        project = RenpyProject(Path(project_path))
        sdk = get_or_install_sdk(version)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # Autopilot launches its own throwaway sessions; free any manual one first.
    stop_game(project_path)
    try:
        return _autopilot(sdk, project, max_runs=max_runs, max_steps=max_steps)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "launch_game",
    "stop_game",
    "stop_all",
    "game_state",
    "advance",
    "list_choices",
    "select_choice",
    "eval_expr",
    "get_var",
    "set_var",
    "poll_events",
    "screenshot_png",
    "run_autopilot",
]
