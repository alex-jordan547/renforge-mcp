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
import math
import os
import signal
import time
from collections import deque
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
    result = {"ok": True, "already_running": False, "current_label": label}
    if session.headless:
        # Running under xvfb-run: no visible window, but the bridge (state,
        # screenshots, input) works normally.
        result["headless"] = True
    return result


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


def game_state(project_path: str, include: list[str] | tuple[str, ...] | None = None) -> dict:
    """Return live state, optionally including compact metrics/audio sections."""
    if include is None:
        return _with_client(project_path, lambda c: {"ok": True, **c.get_state()})
    return _with_client(
        project_path,
        lambda c: {"ok": True, **c.get_state(include=include)},
    )


def inspect_screen(project_path: str, name: str) -> dict:
    """Inspect an active screen's scope and passed arguments."""
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": "screen name is required"}
    return _with_client(project_path, lambda c: c.inspect_screen(name.strip()))


def advance(project_path: str) -> dict:
    return _with_client(project_path, lambda c: c.advance())


def control(project_path: str, action: str) -> dict:
    return _with_client(project_path, lambda c: c.control(action))


def send_input(
    project_path: str,
    *,
    text: str | None = None,
    key: str | None = None,
    scroll: dict[str, Any] | None = None,
    submit: bool = False,
) -> dict:
    """Send exactly one text, named-key, or logical-coordinate scroll input."""
    selected = [value is not None for value in (text, key, scroll)]
    if sum(selected) != 1:
        return {"ok": False, "error": "exactly one of text, key, or scroll is required"}
    if not isinstance(submit, bool):
        return {"ok": False, "error": "submit must be a boolean"}
    if key is not None and submit:
        return {"ok": False, "error": "submit is only valid with text input"}
    if scroll is not None and submit:
        return {"ok": False, "error": "submit is only valid with text input"}
    if text is not None and not isinstance(text, str):
        return {"ok": False, "error": "text must be a string"}
    if key is not None and (not isinstance(key, str) or not key.strip()):
        return {"ok": False, "error": "key must be a non-empty string"}
    if scroll is not None and not isinstance(scroll, dict):
        return {"ok": False, "error": "scroll must be an object with x, y, and direction"}
    return _with_client(
        project_path,
        lambda client: client.send_input(
            text=text,
            key=key,
            scroll=scroll,
            submit=submit,
        ),
    )


def saves(
    project_path: str,
    action: str,
    *,
    slot: str | None = None,
    extra_info: str | None = None,
    regexp: str | None = None,
) -> dict:
    """Save, load, or list named save slots through the running bridge."""
    if action not in {"save", "load", "list"}:
        return {"ok": False, "error": "action must be one of: save, load, list"}

    if action in {"save", "load"}:
        if not isinstance(slot, str) or not slot.strip():
            return {"ok": False, "error": "slot is required for action '%s'" % action}
    elif slot is not None:
        return {"ok": False, "error": "slot is only valid for save or load"}

    if action == "save":
        if regexp is not None:
            return {"ok": False, "error": "regexp is only valid for action 'list'"}
        if extra_info is not None and not isinstance(extra_info, str):
            return {"ok": False, "error": "extra_info must be a string"}
        return _with_client(
            project_path,
            lambda client: client.save_slot(slot, extra_info=extra_info or ""),
        )

    if action == "load":
        if extra_info is not None:
            return {"ok": False, "error": "extra_info is only valid for action 'save'"}
        if regexp is not None:
            return {"ok": False, "error": "regexp is only valid for action 'list'"}
        return _with_client(project_path, lambda client: client.load_slot(slot))

    if extra_info is not None:
        return {"ok": False, "error": "extra_info is only valid for action 'save'"}
    if regexp is not None and not isinstance(regexp, str):
        return {"ok": False, "error": "regexp must be a string"}
    return _with_client(project_path, lambda client: client.list_slots(regexp=regexp))


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


def hover_element(
    project_path: str,
    text: str | None = None,
    id: str | None = None,
    *,
    screen: str | None = None,
    exact: bool = False,
    element_id: str | None = None,
    expected_frame_id: str | None = None,
) -> dict:
    """Move the pointer over a visible control without clicking it."""

    def _handler(client: BridgeClient) -> dict:
        return client.hover_element(
            text=text,
            id=id,
            screen=screen,
            exact=exact,
            element_id=element_id,
            expected_frame_id=expected_frame_id,
        )

    return _with_client(project_path, _handler)


def get_ui_element_bounds(
    project_path: str,
    text: str | None = None,
    id: str | None = None,
    *,
    screen: str | None = None,
    exact: bool = False,
    element_id: str | None = None,
    expected_frame_id: str | None = None,
) -> dict:
    """Return focus and painted bounds for a visible UI element."""
    return _with_client(
        project_path,
        lambda c: c.get_ui_element_bounds(
            text=text,
            id=id,
            screen=screen,
            exact=exact,
            element_id=element_id,
            expected_frame_id=expected_frame_id,
        ),
    )


def get_displayable_bounds(
    project_path: str,
    tag: str,
    *,
    layer: str | None = None,
) -> dict:
    """Return the rendered bounds of a shown image tag in logical coordinates."""
    return _with_client(
        project_path,
        lambda c: c.get_displayable_bounds(tag, layer=layer),
    )


def position_element(
    project_path: str,
    tag: str,
    *,
    layer: str | None = None,
    **placement: float,
) -> dict:
    """Reposition a shown image tag at runtime and return its new bounds."""
    return _with_client(
        project_path,
        lambda c: c.position_element(tag, layer=layer, **placement),
    )


def eval_expr(project_path: str, expr: str) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, "value": c.eval_expr(expr)})


def get_var(project_path: str, name: str) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, "value": c.get_var(name)})


def set_var(project_path: str, name: str, value: Any) -> dict:
    return _with_client(project_path, lambda c: c.set_var(name, value))


def poll_events(project_path: str, since: int = 0) -> dict:
    return _with_client(project_path, lambda c: {"ok": True, **c.poll_events(since)})


def _tail_project_file(project_root: Path, filename: str, max_lines: int = 100) -> dict | None:
    """Read a bounded tail from a project-root diagnostic file."""
    path = (project_root / filename).resolve()
    try:
        path.relative_to(project_root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, maxlen=max_lines)
    except OSError:
        return None
    return {"name": filename, "tail": "".join(lines), "mtime": mtime}


def _error_files(project_path: str) -> dict:
    project_root = Path(project_path).expanduser().resolve()
    files = []
    for filename in ("traceback.txt", "errors.txt", "log.txt"):
        record = _tail_project_file(project_root, filename)
        if record is not None:
            files.append(record)

    result: dict[str, Any] = {"ok": True, "events": [], "files": files}
    session = _SESSIONS.get(_key(project_root))
    if session is not None:
        try:
            exit_code = session.process.poll()
        except Exception:
            exit_code = None
        if exit_code is not None:
            result["exit_code"] = exit_code
    if not files:
        result["message"] = "no errors found"
    return result


def get_errors(project_path: str, since: int = 0) -> dict:
    """Return recent bridge errors or bounded crash diagnostics from disk."""
    try:
        cursor = int(since)
    except (TypeError, ValueError):
        return {"ok": False, "error": "since must be an integer"}
    if cursor < 0:
        return {"ok": False, "error": "since must be non-negative"}

    try:
        reply = _client(project_path).poll_events(cursor)
        events = reply.get("events", [])
        if not isinstance(events, list):
            events = []
        errors = [
            event
            for event in events
            if isinstance(event, dict)
            and str(event.get("type", "")).lower() in {"error", "exception"}
        ]
        return {"ok": True, "events": errors, "cursor": reply.get("cursor", cursor)}
    except Exception:
        return _error_files(project_path)


def wait_until(
    project_path: str,
    *,
    label: str | None = None,
    screen: str | None = None,
    expr: str | None = None,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> dict:
    """Poll exactly one live-game condition until it matches or times out."""
    conditions = [("label", label), ("screen", screen), ("expr", expr)]
    selected = [(name, value) for name, value in conditions if value is not None]
    if len(selected) != 1:
        return {"ok": False, "error": "exactly one of label, screen, expr is required"}
    condition, value = selected[0]
    if not isinstance(value, str) or not value.strip():
        return {"ok": False, "error": "%s must be a non-empty string" % condition}

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return {"ok": False, "error": "timeout must be a finite non-negative number"}
    if not math.isfinite(float(timeout)) or timeout < 0 or timeout > 120:
        return {"ok": False, "error": "timeout must be between 0 and 120 seconds"}
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        return {"ok": False, "error": "interval must be a finite non-negative number"}
    if not math.isfinite(float(interval)) or interval < 0:
        return {"ok": False, "error": "interval must be a finite non-negative number"}

    try:
        client = _client(project_path)
    except FileNotFoundError:
        return {"ok": False, "error": "no running game (bridge not found); call renforge_launch first"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        try:
            state = client.get_state()
            if not isinstance(state, dict):
                return {"ok": False, "error": "bridge state must be an object"}
            if condition == "label":
                matched = state.get("current_label") == value
            elif condition == "screen":
                matched = bool(client.eval_expr("renpy.get_screen(%r) is not None" % value))
            else:
                matched = bool(client.eval_expr(value))
        except BridgeError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        elapsed = time.monotonic() - started
        if matched:
            return {"ok": True, "matched": condition, "elapsed": elapsed, "state": state}
        if elapsed >= float(timeout):
            return {"ok": False, "error": "timeout", "elapsed": elapsed, "state": state}

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": False,
                "error": "timeout",
                "elapsed": elapsed,
                "state": state,
            }
        sleep_interval = float(interval) if interval > 0 else 0.001
        time.sleep(min(sleep_interval, remaining))


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
    "inspect_screen",
    "advance",
    "control",
    "send_input",
    "saves",
    "list_choices",
    "select_choice",
    "list_ui_elements",
    "click_element",
    "click_at",
    "hover_element",
    "get_ui_element_bounds",
    "eval_expr",
    "get_var",
    "set_var",
    "poll_events",
    "get_errors",
    "wait_until",
    "screenshot_png",
    "run_autopilot",
]
