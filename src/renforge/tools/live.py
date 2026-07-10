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

import json
import os
import signal
from pathlib import Path
from typing import Any, Callable

from ..autopilot import autopilot as _autopilot
from ..bridge.client import BridgeClient, BridgeError
from ..bridge.launcher import BridgeSession, launch_with_bridge, remove_bridge_artifacts
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
    if existing is not None:
        # Reuse a live session only when no warp is requested and it still
        # answers; otherwise tear it down (a warp needs a fresh --warp launch,
        # and a dead process must be reaped so it does not linger as a zombie).
        if warp is None and existing.process.poll() is None:
            try:
                state = existing.client.get_state()
                return {"ok": True, "already_running": True, "current_label": state.get("current_label")}
            except Exception:
                pass  # unreachable session; fall through and relaunch
        try:
            existing.close()
        except Exception:
            pass
        _SESSIONS.pop(key, None)

    if warp is None:
        try:
            external = _client(project.root)
            state = external.get_state()
            return {
                "ok": True,
                "already_running": True,
                "external": True,
                "current_label": state.get("current_label"),
            }
        except Exception:
            pass
    else:
        # A dashboard or another MCP process may own the live session. A warp
        # needs a single fresh process, so stop that external session first.
        _stop_external(str(project.root))

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
    if session is not None:
        session.close()
        return {"ok": True, "was_running": True}
    # No in-process session: the game may have been launched elsewhere (e.g. the
    # dashboard server). Stop it through the published bridge.json instead.
    return _stop_external(project_path)


def _stop_external(project_path: str) -> dict:
    """Stop a game launched by another process, using ``bridge.json``.

    Kills the game only after the bridge answers a ``ping`` with the token from
    ``bridge.json`` — that proves the recorded PID is really our game and not a
    recycled/unrelated process. A stale ``bridge.json`` (no live bridge) is just
    cleaned up.
    """
    try:
        project = RenpyProject(Path(project_path))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    info_path = project.root / ".renforge" / "bridge.json"
    if not info_path.exists():
        return {"ok": True, "was_running": False}

    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception:
        info = {}

    alive = False
    try:
        BridgeClient.from_project(project.root, timeout=1.0).ping()
        alive = True
    except Exception:
        alive = False

    was_running = False
    pid = info.get("pid")
    if alive and isinstance(pid, int):
        was_running = _terminate_pid(pid)

    remove_bridge_artifacts(project.root)
    return {"ok": True, "was_running": was_running}


def _terminate_pid(pid: int) -> bool:
    """Force-kill ``pid``. Returns whether it existed."""
    # SIGKILL does not exist on Windows; there os.kill with SIGTERM calls
    # TerminateProcess, which is already an unconditional kill.
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


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


def control(project_path: str, action: str) -> dict:
    return _with_client(project_path, lambda c: c.control(action))


def _filter_narrative_choices(raw_choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only choices that come from the active narrative choice screen."""

    choices: list[dict[str, Any]] = []
    for choice in raw_choices:
        if not isinstance(choice, dict):
            continue
        if choice.get("screen") == "choice":
            choices.append(choice)
    return choices


def list_choices(project_path: str) -> dict:
    def _handler(client: BridgeClient) -> dict:
        state = client.get_state()
        if not bool(state.get("menu")):
            return {"ok": True, "choices": []}

        raw_choices = client.list_choices()
        if not isinstance(raw_choices, list):
            raw_choices = []
        return {"ok": True, "choices": _filter_narrative_choices(raw_choices)}

    return _with_client(project_path, _handler)


def select_choice(project_path: str, text: str | None = None, index: int | None = None) -> dict:
    return _with_client(project_path, lambda c: c.select_choice(text=text, index=index))


def list_ui_elements(
    project_path: str,
    *,
    screen: str | None = None,
    text: str | None = None,
    element_type: str | None = None,
) -> dict:
    """List visible focusable Ren'Py controls and screen-space bounds."""

    def _handler(client: BridgeClient) -> dict:
        info_method = getattr(client, "list_ui_elements_info", None)
        if callable(info_method):
            info = info_method(
                screen=screen,
                text=text,
                element_type=element_type,
            )
        else:
            # Keep dashboard/older bridge fakes compatible while the frame
            # metadata remains available when supported by the bridge.
            info = {
                "elements": client.list_ui_elements(
                    screen=screen,
                    text=text,
                    element_type=element_type,
                )
            }
        return {
            "ok": True,
            **info,
        }

    return _with_client(project_path, _handler)


def click_element(
    project_path: str,
    text: str | None = None,
    id: str | None = None,
    *,
    screen: str | None = None,
    exact: bool = False,
    element_id: str | None = None,
    expected_frame_id: str | None = None,
) -> dict:
    """Click a visible control by semantic text or an ID from list_ui_elements."""
    def _handler(client: BridgeClient) -> dict:
        kwargs: dict[str, Any] = {
            "text": text,
            "id": id,
            "screen": screen,
            "exact": exact,
            "element_id": element_id,
        }
        if expected_frame_id is not None:
            kwargs["expected_frame_id"] = expected_frame_id
        return client.click_element(**kwargs)

    return _with_client(
        project_path,
        _handler,
    )


def click_at(
    project_path: str,
    x: int | float,
    y: int | float,
    *,
    expected_screenshot: str | dict[str, Any] | None = None,
    expected_state: dict[str, Any] | None = None,
    expected_screenshot_hash: str | None = None,
    expected_frame_id: str | None = None,
    coordinate_space: str = "logical",
) -> dict:
    """Click screen coordinates with optional screenshot/state guards."""
    def _handler(client: BridgeClient) -> dict:
        kwargs: dict[str, Any] = {}
        if expected_screenshot is not None:
            kwargs["expected_screenshot"] = expected_screenshot
        if expected_state is not None:
            kwargs["expected_state"] = expected_state
        if expected_screenshot_hash is not None:
            kwargs["expected_screenshot_hash"] = expected_screenshot_hash
        if expected_frame_id is not None:
            kwargs["expected_frame_id"] = expected_frame_id
        kwargs["coordinate_space"] = coordinate_space
        return client.click_at(x, y, **kwargs)

    return _with_client(
        project_path,
        _handler,
    )


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
    "control",
    "list_choices",
    "select_choice",
    "list_ui_elements",
    "click_element",
    "click_at",
    "eval_expr",
    "get_var",
    "set_var",
    "poll_events",
    "screenshot_png",
    "run_autopilot",
]
