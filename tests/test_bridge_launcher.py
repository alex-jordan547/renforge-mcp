import errno
import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from renforge.bridge.launcher import (
    BridgeSession,
    ProjectBridgeLock,
    launch_with_bridge,
    remove_bridge_artifacts,
)
from renforge.launch_env import LaunchError
from renforge.project import RenpyProject
from renforge.sdk import RenpySdk


_LAUNCHER_NAME = "renpy.exe" if os.name == "nt" else "renpy.sh"


class _FakeProcess:
    def __init__(self):
        self.pid = 424242
        self.returncode = None
        self.stdout = None
        self.stderr = None
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.terminated = True

    def kill(self):
        self.returncode = -9
        self.killed = True

    def wait(self, timeout: float | None = None):
        self.waited = True
        self.returncode = 0


class _ResistantProcess(_FakeProcess):
    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout: float | None = None):
        self.waited = True
        if self.returncode is None:
            raise TimeoutError("process is still alive")

    def exit(self):
        self.returncode = 0


class _FakeClient:
    def ping(self):
        return {"ok": True, "pong": True}


def _make_project(
    tmp_path: Path, name: str = "project"
) -> tuple[RenpyProject, RenpySdk, Path]:
    project_root = tmp_path / name
    (project_root / "game").mkdir(parents=True)
    sdk_root = tmp_path / f"sdk-{name}"
    sdk_root.mkdir(parents=True)
    (sdk_root / _LAUNCHER_NAME).write_text("#!/bin/sh\n", encoding="utf-8")
    return RenpyProject(project_root), RenpySdk(version="8.3.7", root=sdk_root), project_root


def _write_bridge_info(project_root: Path, token: str) -> None:
    info_path = project_root / ".renforge" / "bridge.json"
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(json.dumps({"token": token}), encoding="utf-8")


@pytest.mark.parametrize("warp", [None, "game/script.rpy:123"])
def test_launch_with_bridge_builds_run_command(monkeypatch, tmp_path: Path, warp: str | None) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        captured["command"] = command
        _write_bridge_info(project_root, env["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient.from_project", lambda _project_root: _FakeClient())

    session = launch_with_bridge(sdk, project, warp=warp)
    assert session is not None
    command = list(captured["command"])  # type: ignore[arg-type]
    assert len(command) >= 3
    assert command[0].endswith(_LAUNCHER_NAME)
    assert command[1] == str(project_root.resolve())
    if warp is None:
        assert command[2:] == ["run"]
    else:
        assert command[2:5] == ["run", "--warp", warp]
    session.close(timeout=0.1)


def test_launch_without_display_nor_xvfb_fails_fast(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("renforge.bridge.launcher.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("renforge.bridge.launcher.shutil.which", lambda _name: None)
    monkeypatch.setattr("renforge.launch_env.shutil.which", lambda _name: None)
    project, sdk, root = _make_project(tmp_path)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("Popen must not be reached without a display")

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fail_popen)

    with pytest.raises(Exception, match="display") as excinfo:
        launch_with_bridge(sdk, project)

    error = excinfo.value
    assert getattr(error, "code", None) in {None, "DISPLAY_UNAVAILABLE"}
    # Fails before injecting anything: no artifacts to clean up.
    assert not (root / "game" / "renforge_bridge.rpy").exists()


def test_launch_without_display_falls_back_to_xvfb(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("renforge.bridge.launcher.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("renforge.bridge.launcher.shutil.which", lambda _name: "/usr/bin/xvfb-run")
    monkeypatch.setattr("renforge.launch_env.shutil.which", lambda _name: "/usr/bin/xvfb-run")
    project, sdk, project_root = _make_project(tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        captured["command"] = command
        captured["start_new_session"] = start_new_session
        _write_bridge_info(project_root, env["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient.from_project", lambda _project_root: _FakeClient())

    session = launch_with_bridge(sdk, project)
    command = list(captured["command"])  # type: ignore[arg-type]
    assert command[:2] == ["xvfb-run", "-a"]
    assert command[2].endswith(_LAUNCHER_NAME)
    assert captured["start_new_session"] is True
    assert session.headless is True

    # close() must target the process group, not just the xvfb-run wrapper.
    group_kill: dict[str, object] = {}
    kill_signal = getattr(signal, "SIGKILL", 9)
    monkeypatch.setattr(
        "renforge.bridge.launcher.signal.SIGKILL",
        kill_signal,
        raising=False,
    )
    monkeypatch.setattr(
        "renforge.bridge.launcher.os.getpgid",
        lambda _pid: 4242,
        raising=False,
    )
    monkeypatch.setattr(
        "renforge.bridge.launcher.os.killpg",
        lambda pgid, sig: group_kill.update(pgid=pgid, sig=sig),
        raising=False,
    )
    session.close(timeout=0.1)
    assert group_kill == {"pgid": 4242, "sig": kill_signal}


def test_launch_accepts_display_provided_via_extra_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("renforge.bridge.launcher.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    project, sdk, project_root = _make_project(tmp_path)

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        _write_bridge_info(project_root, env["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient.from_project", lambda _project_root: _FakeClient())

    session = launch_with_bridge(sdk, project, extra_env={"DISPLAY": ":99"})
    assert session is not None
    session.close(timeout=0.1)


def test_remove_bridge_artifacts_deletes_injected_and_runtime_files(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    injected = game / "renforge_bridge.rpy"
    injected.write_text("# injected\n", encoding="utf-8")
    (game / "renforge_bridge.rpyc").write_bytes(b"\x00")
    (game / "renforge_bridge.rpyc.bak").write_bytes(b"\x00")
    renforge = tmp_path / ".renforge"
    renforge.mkdir()
    (renforge / "bridge.json").write_text("{}", encoding="utf-8")
    (tmp_path / "traceback.txt").write_text("boom", encoding="utf-8")

    remove_bridge_artifacts(tmp_path)

    assert not injected.exists()
    assert not (game / "renforge_bridge.rpyc").exists()
    assert not (game / "renforge_bridge.rpyc.bak").exists()
    assert not (renforge / "bridge.json").exists()
    assert not (tmp_path / "traceback.txt").exists()

    # Idempotent: a second call on an already-clean tree does not raise.
    remove_bridge_artifacts(tmp_path)


def test_bridge_session_close_kills_running_game(tmp_path: Path) -> None:
    process = _FakeProcess()
    session = BridgeSession(process, _FakeClient(), tmp_path)

    session.close()

    assert process.killed is True
    assert process.terminated is False
    assert session.closed is True

    injected = tmp_path / "game" / "renforge_bridge.rpy"
    injected.parent.mkdir()
    injected.write_text("# belongs to a later session\n", encoding="utf-8")
    session.close()
    assert injected.exists()


def test_failed_launch_removes_every_generated_bridge_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, root = _make_project(tmp_path)

    def fake_popen(*_args, **_kwargs):
        process = _FakeProcess()
        process.returncode = 1
        (root / "game" / "renforge_bridge.rpyc").write_bytes(b"compiled")
        (root / ".renforge").mkdir(exist_ok=True)
        (root / ".renforge" / "bridge.json").write_text("{}", encoding="utf-8")
        (root / "traceback.txt").write_text("no display", encoding="utf-8")
        return process

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)

    with pytest.raises(RuntimeError, match="Game exited"):
        launch_with_bridge(sdk, project)

    assert not (root / "game" / "renforge_bridge.rpy").exists()
    assert not (root / "game" / "renforge_bridge.rpyc").exists()
    assert not (root / ".renforge" / "bridge.json").exists()
    assert not (root / "traceback.txt").exists()



def test_launch_retries_until_ping_returns_pong(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)

    attempts = {"count": 0}

    class _LaggyClient:
        def ping(self):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return {"error": "timeout_waiting_for_main_thread"}
            return {"ok": True, "pong": True}

    def fake_popen(*_args, **_kwargs):
        _write_bridge_info(project_root, _kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient.from_project", lambda _project_root: _LaggyClient())

    session = launch_with_bridge(sdk, project, startup_timeout=5.0)
    assert session is not None
    assert attempts["count"] == 3
    session.close(timeout=0.1)


def test_launch_cancellation_stops_process_and_cleans_artifacts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    cancel_event = threading.Event()
    process = _FakeProcess()

    class _WaitingClient:
        def ping(self):
            cancel_event.set()
            return {"error": "timeout_waiting_for_main_thread"}

    def fake_popen(*_args, **_kwargs):
        _write_bridge_info(project_root, _kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return process

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _WaitingClient(),
    )

    with pytest.raises(Exception) as excinfo:
        launch_with_bridge(
            sdk,
            project,
            startup_timeout=5.0,
            cancel_event=cancel_event,
        )

    assert getattr(excinfo.value, "code", None) == "LAUNCH_CANCELLED"
    assert process.terminated is True
    assert not (project_root / "game" / "renforge_bridge.rpy").exists()
    assert not (project_root / ".renforge" / "bridge.json").exists()


def test_second_launch_same_project_fails_without_touching_first_session(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    popen_calls = {"count": 0}

    def fake_popen(*_args, **kwargs):
        popen_calls["count"] += 1
        _write_bridge_info(project_root, kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )

    first = launch_with_bridge(sdk, project, token="first-token")
    injected_before = (project_root / "game" / "renforge_bridge.rpy").read_bytes()
    manifest_before = (project_root / ".renforge" / "bridge.json").read_bytes()

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, token="second-token")

    assert getattr(excinfo.value, "code", None) == "BRIDGE_PROJECT_LOCKED"
    assert getattr(excinfo.value, "phase", None) == "acquiring_project_lock"
    assert popen_calls["count"] == 1
    assert (project_root / "game" / "renforge_bridge.rpy").read_bytes() == injected_before
    assert (project_root / ".renforge" / "bridge.json").read_bytes() == manifest_before

    first.close(timeout=0.1)
    assert (project_root / ".renforge" / "bridge.lock").exists()


def test_sessions_for_different_projects_are_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project_a, sdk, root_a = _make_project(tmp_path, "project-a")
    project_b, _, root_b = _make_project(tmp_path, "project-b")

    def fake_popen(command, env=None, **_kwargs):
        _write_bridge_info(Path(command[1]), env["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )

    session_a = launch_with_bridge(sdk, project_a, token="token-a")
    session_b = launch_with_bridge(sdk, project_b, token="token-b")
    session_a.close(timeout=0.1)

    assert not (root_a / "game" / "renforge_bridge.rpy").exists()
    assert not (root_a / ".renforge" / "bridge.json").exists()
    assert (root_b / "game" / "renforge_bridge.rpy").exists()
    assert json.loads((root_b / ".renforge" / "bridge.json").read_text())["token"] == "token-b"

    session_b.close(timeout=0.1)


def test_project_lock_is_released_after_cancelled_launch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    cancel_event = threading.Event()
    launches = {"count": 0}

    class _CancellingClient:
        def ping(self):
            cancel_event.set()
            return {"error": "not-ready"}

    def fake_popen(*_args, **kwargs):
        launches["count"] += 1
        _write_bridge_info(project_root, kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _CancellingClient(),
    )

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, cancel_event=cancel_event)
    assert getattr(excinfo.value, "code", None) == "LAUNCH_CANCELLED"

    cancel_event.clear()
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )
    session = launch_with_bridge(sdk, project)
    assert launches["count"] == 2
    session.close(timeout=0.1)


def test_bridge_manifest_with_wrong_token_is_never_accepted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    clock = iter((0.0, 0.0, 2.0))

    def fake_popen(*_args, **_kwargs):
        _write_bridge_info(project_root, "another-session-token")
        return _FakeProcess()

    def fail_from_project(_project_root):
        raise AssertionError("A manifest for another session must not create a client")

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient.from_project", fail_from_project)
    monkeypatch.setattr("renforge.bridge.launcher.time.time", lambda: next(clock))
    monkeypatch.setattr("renforge.bridge.launcher.time.sleep", lambda _seconds: None)

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, token="expected-token", startup_timeout=1.0)

    assert getattr(excinfo.value, "code", None) == "BRIDGE_CONNECTION_TIMEOUT"
    assert not (project_root / ".renforge" / "bridge.json").exists()


def test_close_keeps_lock_and_artifacts_until_process_exit(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    resistant = _ResistantProcess()
    launches = {"count": 0}

    def fake_popen(*_args, **kwargs):
        launches["count"] += 1
        _write_bridge_info(project_root, kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return resistant if launches["count"] == 1 else _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )

    first = launch_with_bridge(sdk, project, token="first-token")
    first_close = first.close(timeout=0.01)

    assert "process_alive" in first_close["failed"]
    assert first.closed is False
    assert (project_root / "game" / "renforge_bridge.rpy").exists()
    assert (project_root / ".renforge" / "bridge.json").exists()
    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, token="blocked-token")
    assert excinfo.value.code == "BRIDGE_PROJECT_LOCKED"
    assert launches["count"] == 1

    resistant.exit()
    retry_close = first.close(timeout=0.01)
    assert retry_close.get("failed") is None
    assert first.closed is True
    assert not (project_root / "game" / "renforge_bridge.rpy").exists()
    assert not (project_root / ".renforge" / "bridge.json").exists()

    second = launch_with_bridge(sdk, project, token="second-token")
    assert launches["count"] == 2
    second.close(timeout=0.01)


def test_failed_launch_escalates_to_kill_before_releasing_lock(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)

    class _DiesOnKill(_ResistantProcess):
        def kill(self):
            super().kill()
            self.returncode = -9

    process = _DiesOnKill()
    cancel_event = threading.Event()
    launches = {"count": 0}

    class _CancellingClient:
        def ping(self):
            cancel_event.set()
            return {"error": "not-ready"}

    def fake_popen(*_args, **kwargs):
        launches["count"] += 1
        _write_bridge_info(project_root, kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return process if launches["count"] == 1 else _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _CancellingClient(),
    )

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, cancel_event=cancel_event)

    assert excinfo.value.code == "LAUNCH_CANCELLED"
    assert process.terminated is True
    assert process.killed is True
    assert process.poll() == -9
    assert not (project_root / "game" / "renforge_bridge.rpy").exists()

    cancel_event.clear()
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )
    session = launch_with_bridge(sdk, project)
    session.close(timeout=0.01)


def test_lock_file_open_permission_error_is_not_reported_as_contention(
    monkeypatch, tmp_path: Path
) -> None:
    original_open = Path.open

    def denied_open(path, *args, **kwargs):
        if path.name == "bridge.lock":
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)

    with pytest.raises(LaunchError) as excinfo:
        ProjectBridgeLock(tmp_path / ".renforge" / "bridge.lock").acquire()

    assert excinfo.value.code == "BRIDGE_PROJECT_LOCK_FAILED"
    assert excinfo.value.phase == "acquiring_project_lock"


def test_failed_launch_defers_cleanup_and_unlock_until_process_exits(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    process = _ResistantProcess()
    cancel_event = threading.Event()
    launches = {"count": 0}

    class _CancellingClient:
        def ping(self):
            cancel_event.set()
            return {"error": "not-ready"}

    def fake_popen(*_args, **kwargs):
        launches["count"] += 1
        _write_bridge_info(project_root, kwargs["env"]["RENFORGE_BRIDGE_TOKEN"])
        return process if launches["count"] == 1 else _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _CancellingClient(),
    )

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, cancel_event=cancel_event)
    assert excinfo.value.code == "LAUNCH_CANCELLED"
    assert (project_root / "game" / "renforge_bridge.rpy").exists()

    with pytest.raises(LaunchError) as locked:
        launch_with_bridge(sdk, project)
    assert locked.value.code == "BRIDGE_PROJECT_LOCKED"
    assert launches["count"] == 1

    process.exit()
    cancel_event.clear()
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )
    deadline = time.monotonic() + 1.0
    while True:
        try:
            session = launch_with_bridge(sdk, project)
            break
        except LaunchError as exc:
            assert exc.code == "BRIDGE_PROJECT_LOCKED"
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)

    assert launches["count"] == 2
    session.close(timeout=0.01)
