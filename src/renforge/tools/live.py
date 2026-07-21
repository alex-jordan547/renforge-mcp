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
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ..autopilot import autopilot as _autopilot
from ..bridge.client import BridgeClient, BridgeError
from ..bridge.launcher import BridgeSession, launch_with_bridge, remove_bridge_artifacts
from ..effect_wait import expected_events_for_action
from ..effect_wait import wait_for_effect as _wait_for_business_effect
from ..launch_env import LaunchError
from ..project import RenpyProject
from ..sdk import get_or_install_sdk
from ..state_compact import (
    compact_state,
    normalize_state_profile,
    validate_limit_args,
)

_SESSIONS: dict[str, BridgeSession] = {}
_LAUNCH_RESPONSE_WAIT_SECONDS = 20.0
_LAUNCH_CANCEL_WAIT_SECONDS = 5.0
_LAUNCH_RESULT_TTL_SECONDS = 300.0
_LAUNCHES: dict[str, "_LaunchTask"] = {}
_LAUNCH_LOCK = threading.Lock()


class _LaunchTask:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.finished: float | None = None
        self.cancel_event = threading.Event()
        self.done_event = threading.Event()
        self.result: dict[str, Any] | None = None


def _prune_launches(now: float) -> None:
    stale_keys = [
        key
        for key, task in _LAUNCHES.items()
        if task.finished is not None
        and now - task.finished >= _LAUNCH_RESULT_TTL_SECONDS
    ]
    for key in stale_keys:
        _LAUNCHES.pop(key, None)


def cancelled_launch_result(*, phase: str = "waiting_for_bridge") -> dict[str, Any]:
    return {
        "ok": False,
        "ready": False,
        "code": "LAUNCH_CANCELLED",
        "phase": phase,
        "message": "Launch was cancelled.",
        "error": "Launch was cancelled.",
    }


def _run_launch(task: _LaunchTask, launch: Callable[[threading.Event], dict]) -> None:
    try:
        task.result = launch(task.cancel_event)
    except Exception as exc:
        task.result = {
            "ok": False,
            "ready": False,
            "code": "LAUNCH_TASK_FAILED",
            "phase": "starting_renpy",
            "message": f"{type(exc).__name__}: {exc}",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        task.finished = time.monotonic()
        task.done_event.set()


def _launch_task_status(task: _LaunchTask) -> dict[str, Any]:
    elapsed_ms = int(((task.finished or time.monotonic()) - task.started) * 1000)
    if not task.done_event.is_set():
        cancel_requested = task.cancel_event.is_set()
        return {
            "ok": True,
            "ready": False,
            "status": "starting",
            "phase": "waiting_for_bridge",
            "elapsed_ms": elapsed_ms,
            "cancel_requested": cancel_requested,
            "message": (
                "Cancellation requested; waiting for the launch task to stop."
                if cancel_requested
                else "Ren'Py is still starting; call renforge_launch_status."
            ),
        }

    result = dict(task.result or {
        "ok": False,
        "code": "LAUNCH_TASK_FAILED",
        "error": "Launch task ended without a result.",
    })
    is_ready = bool(result.get("ok") and result.get("ready", True))
    result["ready"] = is_ready
    result["status"] = "ready" if is_ready else "failed"
    result["elapsed_ms"] = elapsed_ms
    return result


def start_launch(
    project_path: str,
    launch: Callable[[threading.Event], dict],
    *,
    wait_timeout: float = _LAUNCH_RESPONSE_WAIT_SECONDS,
) -> dict[str, Any]:
    key = _key(project_path)
    should_start = False
    with _LAUNCH_LOCK:
        _prune_launches(time.monotonic())
        task = _LAUNCHES.get(key)
        if task is None or task.done_event.is_set():
            task = _LaunchTask()
            _LAUNCHES[key] = task
            should_start = True

    if not should_start:
        result = _launch_task_status(task)
        result.update(
            ok=False,
            code="LAUNCH_IN_PROGRESS",
            message="A launch is already starting; poll status or stop it first.",
            error="A launch is already starting; poll status or stop it first.",
        )
        return result

    threading.Thread(target=_run_launch, args=(task, launch), daemon=True).start()

    task.done_event.wait(max(0.0, wait_timeout))
    return _launch_task_status(task)


def launch_status(project_path: str) -> dict[str, Any]:
    key = _key(project_path)
    with _LAUNCH_LOCK:
        task = _LAUNCHES.get(key)
    if task is not None:
        return _launch_task_status(task)

    try:
        state = _client(project_path).get_state()
    except Exception:
        return {
            "ok": True,
            "ready": False,
            "status": "idle",
            "message": "No launch is active for this project.",
        }
    return {
        "ok": True,
        "ready": True,
        "status": "ready",
        "external": True,
        "current_label": state.get("current_label"),
    }


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


def launch_game(
    project_path: str,
    version: str = "stable",
    warp: str | None = None,
    *,
    display: str = "auto",
    audio: str = "auto",
    savedir: str | None = None,
    persistent: str = "existing",
    cleanup_on_stop: bool = True,
    timeout: float | None = None,
    session: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Launch the project with the bridge injected, or reuse a live session.

    ``display`` / ``audio`` default to ``auto`` (native when available, else
    Xvfb + dummy SDL audio). Pass a ``session`` object or individual kwargs to
    isolate saves (``savedir='temporary'``) and persistent state.
    """
    try:
        project = RenpyProject(Path(project_path))
    except Exception as exc:
        return {
            "ok": False,
            "code": "PROJECT_PATH_UNAVAILABLE",
            "phase": "detecting_environment",
            "error": f"{type(exc).__name__}: {exc}",
            "message": f"{type(exc).__name__}: {exc}",
        }

    session_cfg = dict(session or {})
    savedir = session_cfg.get("savedir", savedir)
    persistent = str(session_cfg.get("persistent", persistent) or "existing")
    if isinstance(session_cfg.get("cleanup_on_stop"), bool):
        cleanup_on_stop = session_cfg["cleanup_on_stop"]
    preferences = str(session_cfg.get("preferences", "existing") or "existing")

    key = _key(project.root)
    existing = _SESSIONS.get(key)
    if existing is not None:
        # Reuse a live session only when no warp is requested and it still
        # answers; otherwise tear it down (a warp needs a fresh --warp launch,
        # and a dead process must be reaped so it does not linger as a zombie).
        if warp is None and existing.process.poll() is None:
            try:
                state = existing.client.get_state()
                return {
                    "ok": True,
                    "already_running": True,
                    "ready": True,
                    "current_label": state.get("current_label"),
                }
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
                "ready": True,
                "current_label": state.get("current_label"),
            }
        except Exception:
            pass
    else:
        # A dashboard or another MCP process may own the live session. A warp
        # needs a single fresh process, so stop that external session first.
        stop_external_game(str(project.root))

    if cancel_event is not None and cancel_event.is_set():
        return cancelled_launch_result(phase="detecting_environment")

    try:
        sdk = get_or_install_sdk(version)
        launch_kwargs: dict[str, object] = {
            "display": display,
            "audio": audio,
            "persistent": persistent,
            "cleanup_on_stop": cleanup_on_stop,
            "preferences": preferences,
        }
        if warp is not None:
            launch_kwargs["warp"] = warp
        if savedir is not None:
            launch_kwargs["savedir"] = savedir
        if timeout is not None:
            launch_kwargs["startup_timeout"] = float(timeout)
        session_obj = launch_with_bridge(sdk, project, cancel_event=cancel_event, **launch_kwargs)
    except LaunchError as exc:
        return exc.to_dict()
    except Exception as exc:
        return {
            "ok": False,
            "code": "RENPY_PROCESS_EXITED",
            "phase": "starting_renpy",
            "error": f"{type(exc).__name__}: {exc}",
            "message": f"{type(exc).__name__}: {exc}",
        }

    _SESSIONS[key] = session_obj
    label = None
    try:
        label = session_obj.client.get_state().get("current_label")
    except Exception:
        pass
    result: dict[str, Any] = {
        "ok": True,
        "ready": True,
        "already_running": False,
        "current_label": label,
        "display": session_obj.display_mode,
        "startup_ms": session_obj.startup_ms,
        "phases": session_obj.phases,
        "environment": session_obj.environment,
    }
    try:
        result["bridge_port"] = session_obj.client._config.port
    except Exception:
        pass
    if session_obj.temporary_savedir is not None:
        result["savedir"] = str(session_obj.temporary_savedir)
    if session_obj.headless:
        # Running under xvfb-run: no visible window, but the bridge (state,
        # screenshots, input) works normally.
        result["headless"] = True
    return result


def stop_game(project_path: str) -> dict:
    key = _key(project_path)
    with _LAUNCH_LOCK:
        task = _LAUNCHES.get(key)

    was_starting = task is not None and not task.done_event.is_set()
    if was_starting:
        task.cancel_event.set()
        if not task.done_event.wait(_LAUNCH_CANCEL_WAIT_SECONDS):
            external = stop_external_game(project_path)
            return {
                "ok": True,
                "was_running": True,
                "launch_cancel_requested": True,
                "external_stopped": bool(external.get("was_running")),
            }

    if task is not None and task.done_event.is_set():
        with _LAUNCH_LOCK:
            if _LAUNCHES.get(key) is task:
                _LAUNCHES.pop(key, None)

    session = _SESSIONS.pop(key, None)
    if session is not None:
        cleaned = session.close()
        return {"ok": True, "was_running": True, **cleaned}
    if (
        was_starting
        and task is not None
        and task.result
        and task.result.get("code") == "LAUNCH_CANCELLED"
    ):
        return {
            "ok": True,
            "was_running": True,
            "launch_cancelled": True,
        }
    # No in-process session: the game may have been launched elsewhere (e.g. the
    # dashboard server). Stop it through the published bridge.json instead.
    return stop_external_game(project_path)


def stop_external_game(project_path: str) -> dict:
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
        reply = BridgeClient.from_project(project.root, timeout=1.0).ping()
        alive = isinstance(reply, dict) and reply.get("pong") is True
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
    with _LAUNCH_LOCK:
        tasks = list(_LAUNCHES.values())
    for task in tasks:
        task.cancel_event.set()
    for task in tasks:
        task.done_event.wait(_LAUNCH_CANCEL_WAIT_SECONDS)
    with _LAUNCH_LOCK:
        _LAUNCHES.clear()
    for session in list(_SESSIONS.values()):
        try:
            session.close()
        except Exception:
            pass
    _SESSIONS.clear()


def game_state(
    project_path: str,
    include: list[str] | tuple[str, ...] | None = None,
    *,
    state_profile: str | None = None,
    max_depth: int = 3,
    max_items: int = 50,
    max_output_bytes: int = 8192,
) -> dict:
    """Return live state, optionally filtered by ``state_profile``.

    When ``state_profile`` is omitted the full bridge payload is returned
    (backward compatible). Pass ``minimal`` / ``interaction`` / ``debug`` to
    drop the bulk store; ``full`` keeps it with serialization limits applied.
    """
    profile = normalize_state_profile(state_profile, default="full")
    if isinstance(profile, dict):
        return profile
    limits = validate_limit_args(
        max_depth=max_depth,
        max_items=max_items,
        max_output_bytes=max_output_bytes,
    )
    if isinstance(limits, dict):
        return limits
    depth, items, budget = limits

    # Metrics/audio remain opt-in via include for the bridge wire format.
    bridge_include = None
    if include is not None:
        sections = [name for name in include if name in ("metrics", "audio")]
        if sections:
            bridge_include = sections

    def _handler(client: BridgeClient) -> dict:
        raw = client.get_state(include=bridge_include)
        if profile == "full" and not include:
            return {"ok": True, **raw}
        state = compact_state(
            raw,
            profile=profile,
            include=include,
            max_depth=depth,
            max_items=items,
            max_output_bytes=budget,
        )
        return {"ok": True, "state_profile": profile, **state}

    return _with_client(project_path, _handler)


def inspect_screen(project_path: str, name: str) -> dict:
    """Inspect an active screen's scope and passed arguments."""
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": "screen name is required"}
    return _with_client(project_path, lambda c: c.inspect_screen(name.strip()))


def advance(project_path: str) -> dict:
    return _with_client(project_path, lambda c: c.advance())


def _next_cursor(project_path: str) -> int:
    try:
        reply = _client(project_path).poll_events(0)
        return int(reply.get("cursor") or 0)
    except Exception:
        return 0


def control(
    project_path: str,
    action: str,
    *,
    interaction_id: str | None = None,
    wait_for_effect: bool = False,
    effect_timeout: float = 5.0,
) -> dict:
    """Run a runtime control action, optionally waiting for a business effect."""

    def _handler(client: BridgeClient) -> dict:
        cursor = 0
        if wait_for_effect:
            try:
                cursor = int(client.poll_events(0).get("cursor") or 0)
            except Exception:
                cursor = 0
        reply = client.control(action, interaction_id=interaction_id)
        if not isinstance(reply, dict):
            return {"ok": False, "error": "control reply must be an object"}
        if reply.get("error") is not None and reply.get("ok") is not False:
            reply = dict(reply)
            reply["ok"] = False
        if wait_for_effect and reply.get("ok", True) and reply.get("error") is None:
            # Prefer the effect already returned by the bridge when present.
            if isinstance(reply.get("effect"), dict):
                return reply
            iid = reply.get("interaction_id") or interaction_id
            expected = expected_events_for_action(action)
            effect = _wait_for_business_effect(
                lambda since: client.poll_events(since),
                since=cursor,
                interaction_id=str(iid) if iid is not None else None,
                expected_types=expected,
                timeout=effect_timeout,
            )
            result = dict(reply)
            if effect.get("ok"):
                result["effect"] = effect.get("effect") or {
                    "event": effect.get("event"),
                }
                result["interaction_id"] = iid
            else:
                result["effect_wait"] = effect
            return result
        return reply

    return _with_client(project_path, _handler)


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
    interaction_id: str | None = None,
    wait_for_effect: bool = False,
    effect_timeout: float = 5.0,
) -> dict:
    """Click a visible control by semantic text or an ID from list_ui_elements.

    When ``wait_for_effect`` is true, poll business events correlated to this
    click (e.g. ``quick_save.completed``) for up to ``effect_timeout`` seconds.
    """

    def _handler(client: BridgeClient) -> dict:
        cursor = 0
        if wait_for_effect:
            try:
                cursor = int(client.poll_events(0).get("cursor") or 0)
            except Exception:
                cursor = 0
        kwargs: dict[str, Any] = {
            "text": text,
            "id": id,
            "screen": screen,
            "exact": exact,
            "element_id": element_id,
        }
        if expected_frame_id is not None:
            kwargs["expected_frame_id"] = expected_frame_id
        if interaction_id is not None:
            kwargs["interaction_id"] = interaction_id
        reply = client.click_element(**kwargs)
        if not wait_for_effect or not isinstance(reply, dict) or not reply.get("ok", True):
            return reply
        iid = reply.get("interaction_id") or interaction_id
        action_name = None
        element = reply.get("element") if isinstance(reply.get("element"), dict) else {}
        action_name = reply.get("action") or element.get("action")
        expected = expected_events_for_action(action_name)
        if not expected:
            return reply
        effect = _wait_for_business_effect(
            lambda since: client.poll_events(since),
            since=cursor,
            interaction_id=str(iid) if iid is not None else None,
            expected_types=expected,
            timeout=effect_timeout,
        )
        result = dict(reply)
        if effect.get("ok"):
            result["effect"] = effect.get("effect") or {"event": effect.get("event")}
            result["interaction_id"] = iid
        else:
            result["effect_wait"] = effect
        return result

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


def hit_test(
    project_path: str,
    x: int | float,
    y: int | float,
    *,
    coordinate_space: str = "logical",
) -> dict:
    """Inspect the interactive focus stack at a logical (or screenshot) point."""

    def _handler(client: BridgeClient) -> dict:
        hit_method = getattr(client, "hit_test", None)
        if not callable(hit_method):
            return {"ok": False, "error": "bridge client does not support hit_test"}
        return hit_method(x, y, coordinate_space=coordinate_space)

    return _with_client(project_path, _handler)


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
    state_profile: str = "interaction",
    include: list[str] | tuple[str, ...] | None = None,
    max_depth: int = 3,
    max_items: int = 50,
    max_output_bytes: int = 8192,
) -> dict:
    """Poll exactly one live-game condition until it matches or times out.

    Returns a compact ``state`` by default (``state_profile='interaction'``).
    Pass ``state_profile='full'`` only when the complete store is required.
    """
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

    profile = normalize_state_profile(state_profile, default="interaction")
    if isinstance(profile, dict):
        return profile
    limits = validate_limit_args(
        max_depth=max_depth,
        max_items=max_items,
        max_output_bytes=max_output_bytes,
    )
    if isinstance(limits, dict):
        return limits
    depth, items, budget = limits

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
        compact = compact_state(
            state,
            profile=profile,
            include=include,
            max_depth=depth,
            max_items=items,
            max_output_bytes=budget,
        )
        matched_info: dict[str, Any] | str = {
            "type": condition,
            "value": value,
        }
        if matched:
            return {
                "ok": True,
                "matched": matched_info,
                "elapsed": elapsed,
                "elapsed_ms": int(elapsed * 1000),
                "state_profile": profile,
                "state": compact,
            }
        if elapsed >= float(timeout):
            return {
                "ok": False,
                "error": "timeout",
                "elapsed": elapsed,
                "elapsed_ms": int(elapsed * 1000),
                "state_profile": profile,
                "state": compact,
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": False,
                "error": "timeout",
                "elapsed": elapsed,
                "elapsed_ms": int(elapsed * 1000),
                "state_profile": profile,
                "state": compact,
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


def _scenario_collect_failure(
    project_path: str,
    *,
    step_index: int,
    expected: Any,
    actual: Any,
    last_action: str | None,
) -> dict[str, Any]:
    """Gather a compact diagnostic payload when a scenario step fails."""
    diag: dict[str, Any] = {
        "failed_step": step_index,
        "expected": expected,
        "actual": actual,
        "last_action": last_action,
    }
    try:
        state = game_state(
            project_path,
            state_profile="interaction",
            max_output_bytes=4096,
        )
        if state.get("ok"):
            diag["state"] = {
                key: state.get(key)
                for key in (
                    "current_label",
                    "menu",
                    "showing_tags",
                    "dialogue",
                    "variables",
                )
                if key in state
            }
            diag["current_label"] = state.get("current_label")
    except Exception as exc:
        diag["state_error"] = f"{type(exc).__name__}: {exc}"
    try:
        choices = list_choices(project_path)
        if choices.get("ok"):
            diag["choices"] = choices.get("choices", [])
    except Exception:
        pass
    try:
        errors = get_errors(project_path)
        if errors.get("ok") and errors.get("events"):
            diag["errors"] = errors.get("events")[:5]
    except Exception:
        pass
    try:
        events = poll_events(project_path)
        if events.get("ok"):
            recent = events.get("events") or []
            if isinstance(recent, list):
                diag["recent_events"] = recent[-5:]
    except Exception:
        pass
    try:
        png = screenshot_png(project_path)
        path = Path(project_path).expanduser().resolve() / ".renforge" / (
            "scenario-failure-step-%s.png" % step_index
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        diag["screenshot"] = str(path)
    except Exception as exc:
        diag["screenshot_error"] = f"{type(exc).__name__}: {exc}"
    return diag


def _scenario_step_timeout(step: dict[str, Any], default: float) -> float:
    for key in ("timeout", "step_timeout"):
        if key in step and step[key] is not None:
            try:
                return float(step[key])
            except (TypeError, ValueError):
                return default
    return default


def run_scenario(
    project_path: str,
    steps: list[dict[str, Any]] | None = None,
    *,
    name: str = "scenario",
    timeout: float = 30.0,
    stop_on_failure: bool = True,
    state_profile: str = "minimal",
    capture_on_failure: bool = True,
) -> dict:
    """Execute a bounded sequence of live interactions and assertions.

    Supported step keys (exactly one action per step): set, eval, click,
    click_at, advance, scroll, wait, assert, select_choice, capture, save, load,
    control, send_input.
    """
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        return {"ok": False, "error": "steps must be a list"}
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return {"ok": False, "error": "timeout must be a finite non-negative number"}
    if not math.isfinite(float(timeout)) or timeout < 0 or timeout > 600:
        return {"ok": False, "error": "timeout must be between 0 and 600 seconds"}

    profile = normalize_state_profile(state_profile, default="minimal")
    if isinstance(profile, dict):
        return profile

    started = time.monotonic()
    deadline = started + float(timeout)
    results: list[dict[str, Any]] = []
    last_action: str | None = None
    overall_ok = True

    action_keys = (
        "set",
        "eval",
        "click",
        "click_at",
        "advance",
        "scroll",
        "wait",
        "assert",
        "select_choice",
        "capture",
        "save",
        "load",
        "control",
        "send_input",
    )

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return {"ok": False, "error": "each step must be an object", "steps": results}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            overall_ok = False
            results.append({"index": index, "status": "failed", "error": "global timeout"})
            break

        present = [key for key in action_keys if key in step]
        if len(present) != 1:
            overall_ok = False
            results.append(
                {
                    "index": index,
                    "status": "failed",
                    "error": "exactly one action key required: %s" % ", ".join(action_keys),
                }
            )
            if stop_on_failure:
                break
            continue

        action = present[0]
        payload = step[action]
        step_timeout = min(_scenario_step_timeout(step, min(15.0, remaining)), remaining)
        step_started = time.monotonic()
        step_result: dict[str, Any] = {"index": index, "action": action}
        try:
            if action == "set":
                if not isinstance(payload, dict):
                    raise ValueError("set payload must be an object of name→value")
                for var_name, var_value in payload.items():
                    set_var(project_path, str(var_name), var_value)
                last_action = "set(%s)" % ", ".join(str(k) for k in payload)
                step_result["status"] = "passed"
            elif action == "eval":
                expr = payload if isinstance(payload, str) else (payload or {}).get("expr")
                if not expr:
                    raise ValueError("eval requires an expression string")
                reply = eval_expr(project_path, str(expr))
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "eval failed")
                step_result["status"] = "passed"
                step_result["value"] = reply.get("value")
                last_action = "eval"
            elif action == "click":
                target = payload if isinstance(payload, dict) else {"text": payload}
                reply = click_element(
                    project_path,
                    text=target.get("text"),
                    id=target.get("id") or target.get("target"),
                    screen=target.get("screen"),
                    exact=bool(target.get("exact", False)),
                    element_id=target.get("element_id"),
                    expected_frame_id=target.get("expected_frame_id"),
                )
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "click failed")
                step_result["status"] = "passed"
                step_result["clicked"] = reply.get("id") or reply.get("text")
                last_action = "click(%s)" % (step_result["clicked"],)
            elif action == "click_at":
                coords = payload if isinstance(payload, dict) else {}
                reply = click_at(
                    project_path,
                    coords.get("x"),
                    coords.get("y"),
                    coordinate_space=str(coords.get("coordinate_space") or "logical"),
                    expected_frame_id=coords.get("expected_frame_id"),
                )
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "click_at failed")
                step_result["status"] = "passed"
                last_action = "click_at(%s,%s)" % (coords.get("x"), coords.get("y"))
            elif action == "advance":
                count = 1
                if isinstance(payload, dict):
                    count = int(payload.get("count") or 1)
                elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
                    count = int(payload)
                for _ in range(max(1, count)):
                    reply = advance(project_path)
                    if not reply.get("ok", True) and reply.get("error"):
                        raise RuntimeError(reply.get("error"))
                step_result["status"] = "passed"
                last_action = "advance(%s)" % count
            elif action == "scroll":
                scroll = payload if isinstance(payload, dict) else {}
                reply = send_input(project_path, scroll=scroll)
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "scroll failed")
                step_result["status"] = "passed"
                last_action = "scroll"
            elif action == "wait":
                wait_payload = payload if isinstance(payload, dict) else {}
                reply = wait_until(
                    project_path,
                    label=wait_payload.get("label"),
                    screen=wait_payload.get("screen"),
                    expr=wait_payload.get("expr"),
                    timeout=step_timeout,
                    interval=float(wait_payload.get("interval") or 0.25),
                    state_profile=str(wait_payload.get("state_profile") or profile),
                    include=wait_payload.get("include"),
                )
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "wait failed")
                step_result["status"] = "passed"
                step_result["matched"] = reply.get("matched")
                last_action = "wait"
            elif action == "assert":
                assertion = payload if isinstance(payload, dict) else {"expr": payload}
                expr = assertion.get("expr")
                if not expr:
                    raise ValueError("assert requires expr")
                reply = eval_expr(project_path, str(expr))
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "assert eval failed")
                actual = reply.get("value")
                expected = assertion.get("equals", True)
                message = assertion.get("message") or str(expr)
                if actual != expected and not (expected is True and bool(actual)):
                    raise AssertionError("%s (actual=%r expected=%r)" % (message, actual, expected))
                step_result["status"] = "passed"
                step_result["actual"] = actual
                last_action = "assert"
            elif action == "select_choice":
                choice = payload if isinstance(payload, dict) else {"text": payload}
                reply = select_choice(
                    project_path,
                    text=choice.get("text"),
                    index=choice.get("index"),
                )
                if not reply.get("ok", True) and reply.get("error"):
                    raise RuntimeError(reply.get("error"))
                step_result["status"] = "passed"
                last_action = "select_choice"
            elif action == "capture":
                png = screenshot_png(project_path)
                label = "scenario-capture"
                if isinstance(payload, dict) and payload.get("name"):
                    label = str(payload["name"])
                elif isinstance(payload, str) and payload:
                    label = payload
                path = Path(project_path).expanduser().resolve() / ".renforge" / ("%s.png" % label)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(png)
                step_result["status"] = "passed"
                step_result["path"] = str(path)
                last_action = "capture"
            elif action == "save":
                slot = payload if isinstance(payload, str) else (payload or {}).get("slot")
                if not slot:
                    raise ValueError("save requires slot")
                reply = saves(project_path, "save", slot=str(slot))
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "save failed")
                step_result["status"] = "passed"
                last_action = "save(%s)" % slot
            elif action == "load":
                slot = payload if isinstance(payload, str) else (payload or {}).get("slot")
                if not slot:
                    raise ValueError("load requires slot")
                reply = saves(project_path, "load", slot=str(slot))
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "load failed")
                step_result["status"] = "passed"
                last_action = "load(%s)" % slot
            elif action == "control":
                act = payload if isinstance(payload, str) else (payload or {}).get("action")
                if not act:
                    raise ValueError("control requires action")
                reply = control(project_path, str(act))
                if not reply.get("ok", True) and reply.get("error"):
                    raise RuntimeError(reply.get("error"))
                step_result["status"] = "passed"
                last_action = "control(%s)" % act
            elif action == "send_input":
                data = payload if isinstance(payload, dict) else {"text": payload}
                reply = send_input(
                    project_path,
                    text=data.get("text"),
                    key=data.get("key"),
                    scroll=data.get("scroll"),
                    submit=bool(data.get("submit", False)),
                )
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "send_input failed")
                step_result["status"] = "passed"
                last_action = "send_input"
            else:
                raise ValueError("unsupported action: %s" % action)
        except Exception as exc:
            overall_ok = False
            step_result["status"] = "failed"
            step_result["error"] = str(exc)
            if capture_on_failure:
                step_result["diagnostics"] = _scenario_collect_failure(
                    project_path,
                    step_index=index,
                    expected=payload if action == "assert" else action,
                    actual=str(exc),
                    last_action=last_action,
                )
            results.append(
                {
                    **step_result,
                    "duration_ms": int((time.monotonic() - step_started) * 1000),
                }
            )
            if stop_on_failure:
                break
            continue

        step_result["duration_ms"] = int((time.monotonic() - step_started) * 1000)
        results.append(step_result)

    duration_ms = int((time.monotonic() - started) * 1000)
    passed = sum(1 for item in results if item.get("status") == "passed")
    failed = sum(1 for item in results if item.get("status") == "failed")
    report: dict[str, Any] = {
        "ok": overall_ok and failed == 0,
        "scenario": name,
        "passed": passed,
        "failed": failed,
        "duration_ms": duration_ms,
        "steps": results,
    }
    if not report["ok"]:
        for item in results:
            if item.get("status") == "failed":
                report["failed_step"] = item.get("index")
                if "diagnostics" in item:
                    report.update(
                        {
                            key: value
                            for key, value in item["diagnostics"].items()
                            if key not in report
                        }
                    )
                break
    return report


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
    "hit_test",
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
    "run_scenario",
]
