"""Tests for stopping a game through the published bridge (cross-process)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from renforge.tools import live


def _make_project(tmp_path: Path, *, with_legacy_bridge: bool = False) -> Path:
    project = tmp_path / "project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    if with_legacy_bridge:
        renforge = project / ".renforge"
        renforge.mkdir(parents=True)
        (renforge / "bridge.json").write_text(
            json.dumps({"host": "127.0.0.1", "port": 65123, "token": "t", "pid": 4242}),
            encoding="utf-8",
        )
        (project / "game" / "renforge_bridge.rpy").write_text("# injected\n", encoding="utf-8")
        (project / "game" / "renforge_bridge.rpyc").write_bytes(b"\x00")
    return project


def test_registry_key_applies_normcase(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path)
    monkeypatch.setattr(
        live.os.path,
        "normcase",
        lambda value: f"normalized:{value}",
    )

    assert live._key(project) == f"normalized:{project.resolve()}"


class _QuitClient:
    def __init__(self) -> None:
        self.control_calls: list[str] = []

    def control(self, action: str, **_kwargs) -> dict:
        self.control_calls.append(action)
        return {"ok": True, "action": action}


class _AliveClient(_QuitClient):
    def ping(self) -> dict:
        return {"ok": True, "pong": True}


class _DeadClient:
    def control(self, action: str, **_kwargs) -> dict:
        raise ConnectionRefusedError("no bridge")

    def ping(self) -> dict:
        raise ConnectionRefusedError("no bridge")


class _StateClient(_AliveClient):
    def get_state(self) -> dict:
        return {"current_label": "dashboard_scene"}


class _LaunchedSession:
    display_mode = "native"
    startup_ms = 10
    phases: list[dict] = []
    environment: dict = {}
    temporary_savedir = None
    headless = False

    def __init__(self, *, editor: bool = False) -> None:
        self.client = _StateClient()
        self.editor = editor


class _RunningProcess:
    def poll(self) -> None:
        return None


class _OwnedRunningSession:
    def __init__(self, *, editor: bool = False) -> None:
        self.editor = editor
        self.client = _StateClient()
        self.process = _RunningProcess()
        self.close_calls = 0
        self.closed = False

    def close(self) -> dict:
        self.close_calls += 1
        self.closed = True
        return {"cleaned": {"renpy_process": True}, "failed": []}


class _RetryingSession:
    def __init__(self, *, process_exited: bool = False, editor: bool = True) -> None:
        self.process_exited = process_exited
        self.editor = editor
        self.closed = False
        self.close_calls = 0

    def close(self) -> dict:
        self.close_calls += 1
        if not self.process_exited:
            return {"cleaned": {}, "failed": ["process_alive"]}
        self.closed = True
        return {"cleaned": {"renpy_process": True}, "failed": []}


class _RaisingSession:
    closed = False
    editor = True

    def close(self) -> dict:
        raise RuntimeError("teardown failed")


def _wait_for_launch_status(
    project: Path,
    expected: str,
    *,
    timeout: float = 1.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = live.launch_status(str(project))
        if result["status"] == expected:
            return result
        time.sleep(0.01)
    raise AssertionError(f"launch status did not become {expected!r}")


def test_stop_game_without_bridge_is_noop(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert live.stop_game(str(project)) == {"ok": True, "was_running": False}


def test_stop_game_issues_authenticated_quit_when_bridge_alive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path, with_legacy_bridge=True)
    client = _QuitClient()
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: client),
    )

    result = live.stop_game(str(project))

    assert result == {"ok": True, "was_running": True}
    assert client.control_calls == ["quit"]
    assert (project / ".renforge" / "bridge.json").exists()
    assert (project / "game" / "renforge_bridge.rpy").exists()
    assert (project / "game" / "renforge_bridge.rpyc").exists()


def test_stop_game_ignores_legacy_bridge_metadata_without_live_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path, with_legacy_bridge=True)

    def missing_client(cls, root, *, timeout=5.0):
        raise live.BridgeError("bridge metadata failed validation")

    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(missing_client),
    )

    result = live.stop_game(str(project))

    assert result == {"ok": True, "was_running": False}
    assert (project / ".renforge" / "bridge.json").exists()
    assert (project / "game" / "renforge_bridge.rpy").exists()
    assert (project / "game" / "renforge_bridge.rpyc").exists()


def test_external_stop_does_not_preacquire_owner_held_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path, with_legacy_bridge=True)
    client = _QuitClient()
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: client),
    )

    # Owner-held lock must not block authenticated quit.
    from renforge.bridge.launcher import ProjectBridgeLock

    project_lock = ProjectBridgeLock(project)
    project_lock.acquire()
    try:
        result = live.stop_external_game(str(project))
    finally:
        project_lock.release()

    assert result == {"ok": True, "was_running": True}
    assert client.control_calls == ["quit"]
    assert (project / ".renforge" / "bridge.json").exists()
    assert (project / "game" / "renforge_bridge.rpy").exists()
    assert (project / "game" / "renforge_bridge.rpyc").exists()


def test_external_stop_reports_not_running_when_quit_cannot_authenticate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path, with_legacy_bridge=True)
    client = _DeadClient()
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: client),
    )

    result = live.stop_external_game(str(project))

    assert result == {
        "ok": False,
        "error": "ConnectionRefusedError: no bridge",
        "was_running": False,
    }
    assert (project / ".renforge" / "bridge.json").exists()


def test_external_stop_requires_explicit_authenticated_quit_ack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path, with_legacy_bridge=True)

    class CustomReplyClient:
        def __init__(self, reply):
            self.reply = reply

        def control(self, action: str, **_kwargs):
            return self.reply

    # Test empty dict reply
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: CustomReplyClient({})),
    )
    result = live.stop_external_game(str(project))
    assert result == {"ok": False, "error": "bridge quit failed", "was_running": False}

    # Test missing or mismatched action reply
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: CustomReplyClient({"ok": True, "action": "wrong"})),
    )
    result = live.stop_external_game(str(project))
    assert result == {"ok": False, "error": "bridge quit failed", "was_running": False}

    # Test explicit error reply
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: CustomReplyClient({"ok": False, "error": "custom failure"})),
    )
    result = live.stop_external_game(str(project))
    assert result == {"ok": False, "error": "custom failure", "was_running": False}

    # Test valid quit ack
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=5.0: CustomReplyClient({"ok": True, "action": "quit"})),
    )
    result = live.stop_external_game(str(project))
    assert result == {"ok": True, "was_running": True}



def test_launch_reuses_a_game_started_by_the_dashboard(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path)
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, **_kwargs: _StateClient()),
    )
    monkeypatch.setattr(
        live,
        "get_or_install_sdk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not relaunch")),
    )

    result = live.launch_game(str(project))

    assert result["ok"] is False
    assert result["ready"] is False
    assert result["code"] == "SESSION_MODE_MISMATCH"
    assert result["requested_editor"] is True
    assert result["existing_editor"] is False
    assert "cannot be proven for an external session" in result["message"]


def test_launch_reuses_owned_session_only_when_editor_mode_matches(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    key = live._key(project)
    session = _OwnedRunningSession(editor=True)
    live._SESSIONS[key] = session

    mismatch = live.launch_game(str(project), editor=False)

    assert mismatch == {
        "ok": True,
        "already_running": True,
        "ready": True,
        "current_label": "dashboard_scene",
        "editor": True,
    }
    assert live._SESSIONS[key] is session
    assert session.close_calls == 0

    matched = live.launch_game(str(project), editor=True)
    assert matched == {
        "ok": True,
        "already_running": True,
        "ready": True,
        "current_label": "dashboard_scene",
        "editor": True,
    }
    assert live._SESSIONS[key] is session
    assert session.close_calls == 0
    live._SESSIONS.pop(key, None)


def test_launch_rejects_editor_mode_for_unproven_external_bridge(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path)
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, **_kwargs: _StateClient()),
    )
    monkeypatch.setattr(
        live,
        "get_or_install_sdk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not relaunch")),
    )

    result = live.launch_game(str(project), editor=True)

    assert result["ok"] is False
    assert result["ready"] is False
    assert result["code"] == "SESSION_MODE_MISMATCH"
    assert result["requested_editor"] is True
    assert result["existing_editor"] is False
    assert "cannot be proven for an external session" in result["message"]


def test_new_launch_passes_editor_mode_to_bridge_launcher(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path)
    launch_kwargs: dict[str, object] = {}
    monkeypatch.setattr(live, "get_or_install_sdk", lambda *_args, **_kwargs: "sdk")

    def fake_launch_with_bridge(_sdk, _project, **kwargs):
        launch_kwargs.update(kwargs)
        return _LaunchedSession(editor=bool(kwargs.get("editor")))

    monkeypatch.setattr(live, "launch_with_bridge", fake_launch_with_bridge)

    result = live.launch_game(str(project), editor=True)

    assert result["ok"] is True
    assert result["editor"] is True
    assert launch_kwargs["editor"] is True

    live._SESSIONS.pop(live._key(project), None)
    launch_kwargs.clear()

    result = live.launch_game(str(project), editor=False)
    assert result["ok"] is True
    assert result["editor"] is True
    assert launch_kwargs["editor"] is True

    live._SESSIONS.pop(live._key(project), None)


def test_launch_status_preserves_editor_for_owned_session(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    key = live._key(project)
    session = _OwnedRunningSession(editor=True)
    live._SESSIONS[key] = session

    result = live.launch_status(str(project))

    assert result == {
        "ok": True,
        "ready": True,
        "status": "ready",
        "current_label": "dashboard_scene",
        "editor": True,
    }
    live._SESSIONS.pop(key, None)


def test_warp_refuses_to_stop_a_live_external_bridge(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path)
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, **_kwargs: _StateClient()),
    )
    monkeypatch.setattr(
        live,
        "stop_external_game",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not stop another bridge owner")
        ),
    )
    monkeypatch.setattr(
        live,
        "get_or_install_sdk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not resolve an SDK while the project is locked")
        ),
    )

    result = live.launch_game(str(project), warp="chapter_one")

    assert result["ok"] is False
    assert result["ready"] is False
    assert result["code"] == "BRIDGE_PROJECT_LOCKED"
    assert result["phase"] == "acquiring_project_lock"
    assert "owning RenForge session" in result["message"]
    assert result["error"] == result["message"]


def test_new_launch_resolves_sdk_for_exact_project_root(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path)
    project_path = project / ".." / project.name
    resolved: dict[str, object] = {}

    def fake_resolve(version: str, *, project_root: Path):
        resolved["version"] = version
        resolved["project_root"] = project_root
        return "sdk"

    monkeypatch.setattr(live, "get_or_install_sdk", fake_resolve)
    monkeypatch.setattr(
        live,
        "launch_with_bridge",
        lambda sdk, renpy_project, **_kwargs: _LaunchedSession(),
    )

    result = live.launch_game(str(project_path), version="8.5.3")

    assert result["ok"] is True
    assert resolved == {
        "version": "8.5.3",
        "project_root": project.resolve(),
    }
    live._SESSIONS.pop(live._key(project), None)


def test_warp_retries_incomplete_existing_session_teardown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path)
    key = live._key(project)
    session = _RetryingSession()
    live._SESSIONS[key] = session
    monkeypatch.setattr(
        live,
        "get_or_install_sdk",
        lambda *_args, **_kwargs: "sdk",
    )
    monkeypatch.setattr(
        live,
        "launch_with_bridge",
        lambda *_args, **_kwargs: _LaunchedSession(),
    )

    first = live.launch_game(str(project), warp="chapter_one")

    assert first["ok"] is False
    assert first["code"] == "BRIDGE_TEARDOWN_INCOMPLETE"
    assert first["phase"] == "stopping_existing_session"
    assert first["failed"] == ["process_alive"]
    assert live._SESSIONS[key] is session
    session.process_exited = True

    second = live.launch_game(str(project), warp="chapter_one")

    assert second["ok"] is True
    assert session.close_calls == 2
    assert live._SESSIONS[key] is not session
    live._SESSIONS.pop(key, None)


def test_warp_keeps_existing_session_when_teardown_raises(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    key = live._key(project)
    session = _RaisingSession()
    live._SESSIONS[key] = session

    result = live.launch_game(str(project), warp="chapter_one")

    assert result["ok"] is False
    assert result["code"] == "BRIDGE_TEARDOWN_INCOMPLETE"
    assert result["phase"] == "stopping_existing_session"
    assert result["failed"] == ["close"]
    assert "RuntimeError: teardown failed" in result["error"]
    assert live._SESSIONS[key] is session
    live._SESSIONS.pop(key, None)


def test_start_launch_returns_before_slow_startup_and_exposes_ready_status(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    release = threading.Event()
    started = threading.Event()

    def delayed_launch(project_root: Path, _cancel_event: threading.Event) -> dict:
        assert project_root == project.resolve()
        started.set()
        assert release.wait(2.0)
        return {"ok": True, "ready": True, "current_label": "main_menu"}

    result = live.start_launch(str(project), delayed_launch, wait_timeout=0.0)

    assert started.wait(1.0)
    assert result["ok"] is True
    assert result["ready"] is False
    assert result["status"] == "starting"

    ignored_launch = threading.Event()
    conflict = live.start_launch(
        str(project),
        lambda _project_root, _cancel_event: ignored_launch.set() or {"ok": True, "ready": True},
        wait_timeout=0.0,
    )
    assert conflict["ok"] is False
    assert conflict["code"] == "LAUNCH_IN_PROGRESS"
    assert conflict["status"] == "starting"
    assert ignored_launch.is_set() is False

    release.set()
    assert _wait_for_launch_status(project, "ready")["ready"] is True
    live.stop_game(str(project))


def test_start_launch_defaults_editor_false(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    release = threading.Event()
    started = threading.Event()

    def delayed_launch(_project_root: Path, _cancel_event: threading.Event) -> dict:
        started.set()
        assert release.wait(2.0)
        return {"ok": True, "ready": True, "current_label": "main_menu"}

    result = live.start_launch(str(project), delayed_launch, wait_timeout=0.0)

    assert started.wait(1.0)
    assert result["ok"] is True
    assert result["ready"] is False
    assert result["status"] == "starting"
    assert result["editor"] is False

    release.set()
    final = _wait_for_launch_status(project, "ready")
    assert final["ok"] is True
    assert final["ready"] is True
    assert final["editor"] is False
    live.stop_game(str(project))


def test_start_launch_exposes_requested_editor_true_in_status_and_result(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    release = threading.Event()
    started = threading.Event()

    def delayed_launch(_project_root: Path, _cancel_event: threading.Event) -> dict:
        started.set()
        assert release.wait(2.0)
        return {"ok": True, "ready": True, "current_label": "main_menu"}

    result = live.start_launch(
        str(project),
        delayed_launch,
        editor=True,
        wait_timeout=0.0,
    )

    assert started.wait(1.0)
    assert result["ok"] is True
    assert result["ready"] is False
    assert result["status"] == "starting"
    assert result["editor"] is True

    release.set()
    final = _wait_for_launch_status(project, "ready")
    assert final["ok"] is True
    assert final["ready"] is True
    assert final["editor"] is True
    live.stop_game(str(project))


def test_launch_status_exposes_a_failed_startup(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    result = live.start_launch(
        str(project),
        lambda _project_root, _cancel_event: {
            "ok": False,
            "code": "RENPY_PROCESS_EXITED",
            "error": "Game exited during startup.",
        },
        wait_timeout=1.0,
    )

    assert result["ok"] is False
    assert result["ready"] is False
    assert result["status"] == "failed"
    live.stop_game(str(project))


def test_stop_game_cancels_a_pending_launch(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    started = threading.Event()

    def cancellable_launch(_project_root: Path, cancel_event: threading.Event) -> dict:
        started.set()
        assert cancel_event.wait(2.0)
        return live.cancelled_launch_result()

    result = live.start_launch(str(project), cancellable_launch, wait_timeout=0.0)

    assert started.wait(1.0)
    assert result["status"] == "starting"
    assert live.stop_game(str(project)) == {
        "ok": True,
        "was_running": True,
        "launch_cancelled": True,
    }
    idle = live.launch_status(str(project))
    assert idle["ok"] is True
    assert idle["status"] == "idle"


def test_stop_game_keeps_a_partial_session_registered_for_retry(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    key = live._key(project)
    session = _RetryingSession()
    live._SESSIONS[key] = session

    first = live.stop_game(str(project))

    assert first["failed"] == ["process_alive"]
    assert live._SESSIONS[key] is session
    session.process_exited = True

    second = live.stop_game(str(project))

    assert second["failed"] == []
    assert session.close_calls == 2
    assert key not in live._SESSIONS


def test_stop_all_keeps_partial_sessions_registered_for_retry(tmp_path: Path) -> None:
    partial_key = live._key(tmp_path / "partial")
    closed_key = live._key(tmp_path / "closed")
    partial = _RetryingSession()
    closed = _RetryingSession(process_exited=True)
    live._SESSIONS[partial_key] = partial
    live._SESSIONS[closed_key] = closed

    live.stop_all()

    assert live._SESSIONS[partial_key] is partial
    assert closed_key not in live._SESSIONS
    partial.process_exited = True
    live.stop_all()
    assert partial_key not in live._SESSIONS


def test_stop_game_attempts_external_stop_when_cancellation_is_still_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path)
    release = threading.Event()
    started = threading.Event()

    def slow_cancel(_project_root: Path, _cancel_event: threading.Event) -> dict:
        started.set()
        assert release.wait(2.0)
        return live.cancelled_launch_result()

    live.start_launch(str(project), slow_cancel, wait_timeout=0.0)
    assert started.wait(1.0)
    monkeypatch.setattr(live, "_LAUNCH_CANCEL_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(
        live,
        "stop_external_game",
        lambda _project_path: {"ok": True, "was_running": True},
    )

    result = live.stop_game(str(project))

    assert result["ok"] is True
    assert result["was_running"] is True
    assert result["launch_cancel_requested"] is True
    assert result["external_stopped"] is True
    pending = live.launch_status(str(project))
    assert pending["status"] == "starting"
    assert pending["cancel_requested"] is True
    release.set()
    assert _wait_for_launch_status(project, "failed")["code"] == "LAUNCH_CANCELLED"
    live.stop_game(str(project))
