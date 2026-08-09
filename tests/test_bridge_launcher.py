import errno
import hashlib
import json
import os
import signal
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from renforge.bridge.launcher import (
    BridgeSession,
    ProjectBridgeLock,
    launch_with_bridge,
    remove_bridge_artifacts,
)
from renforge.editor import EditorEndpoint, PROTOCOL_VERSION
from renforge.editor.exceptions import EditorError
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


def _valid_token(label: str = "token") -> str:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    return digest


def _bridge_info_path(project_root: Path) -> Path:
    return project_root / ".renforge" / "control" / "bridge.json"


def _artifacts_path(project_root: Path) -> Path:
    return project_root / ".renforge" / "control" / "artifacts.json"


def _owned_bridge_rpy(project_root: Path) -> Path:
    matches = sorted((project_root / "game").glob("zzrenforge_bridge_*.rpy"))
    assert matches, f"no owned bridge inject under {project_root / 'game'}"
    return matches[0]


def _load_artifacts(project_root: Path) -> dict:
    return json.loads(_artifacts_path(project_root).read_text(encoding="utf-8"))



def _write_bridge_info(
    project_root: Path,
    env: dict[str, str] | None = None,
    *,
    token: str | None = None,
    session_id: str | None = None,
    state: str = "ready",
    port: int = 40123,
) -> None:
    """Publish validated control-dir bridge metadata for fake subprocesses."""
    if env is not None:
        token = env["RENFORGE_BRIDGE_TOKEN"]
        session_id = env["RENFORGE_BRIDGE_SESSION_ID"]
        root = Path(env["RENFORGE_BRIDGE_PROJECT_ROOT"])
    else:
        if token is None or session_id is None:
            raise ValueError("token and session_id are required without env")
        root = Path(project_root).resolve()
    if state == "starting":
        port = 0
    payload = {
        "schema_version": 1,
        "protocol_version": 1,
        "state": state,
        "session_id": session_id,
        "project_root": str(root.resolve()),
        "host": "127.0.0.1",
        "port": port,
        "token": token,
    }
    info_path = _bridge_info_path(root)
    info_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(info_path.parent, 0o700)
        except OSError:
            pass
    info_path.write_text(json.dumps(payload), encoding="utf-8")
    if os.name != "nt":
        os.chmod(info_path, 0o600)


@pytest.mark.parametrize("warp", [None, "game/script.rpy:123"])
def test_launch_with_bridge_builds_run_command(monkeypatch, tmp_path: Path, warp: str | None) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        captured["command"] = command
        captured["env"] = env
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

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
    env = captured["env"]
    assert isinstance(env, dict)
    assert len(env["RENFORGE_BRIDGE_SESSION_ID"]) == 32
    assert all(ch in "0123456789abcdef" for ch in env["RENFORGE_BRIDGE_SESSION_ID"])
    assert len(env["RENFORGE_BRIDGE_TOKEN"]) == 64
    assert all(ch in "0123456789abcdef" for ch in env["RENFORGE_BRIDGE_TOKEN"])
    assert env["RENFORGE_BRIDGE_PROJECT_ROOT"] == str(project_root.resolve())
    starting = project_root / ".renforge" / "control" / "bridge.json"
    # reserved before spawn; fake popen overwrote it to ready for the ping path
    assert starting.exists()
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
    assert not list((root / "game").glob("zzrenforge_bridge_*.rpy"))


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
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

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
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    session = launch_with_bridge(sdk, project, extra_env={"DISPLAY": ":99"})
    assert session is not None
    session.close(timeout=0.1)


def test_remove_bridge_artifacts_deletes_injected_and_runtime_files(tmp_path: Path) -> None:
    from renforge.bridge.artifacts import allocate_and_materialize, artifacts_path
    from renforge.project import RenpyProject

    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    # Unowned legacy names and traceback must survive cleanup.
    legacy_inject = game / "renforge_bridge.rpy"
    legacy_inject.write_text("# unowned\n", encoding="utf-8")
    (game / "renforge_bridge.rpyc").write_bytes(b"\x00")
    (tmp_path / "traceback.txt").write_text("boom", encoding="utf-8")
    renforge = tmp_path / ".renforge"
    renforge.mkdir(parents=True, exist_ok=True)
    legacy = renforge / "bridge.json"
    legacy.write_text('{"legacy":true}', encoding="utf-8")

    project = RenpyProject(tmp_path)
    materialized = allocate_and_materialize(
        project,
        bridge_payload=b"init python:\n    pass\n",
        include_session_init=False,
        editor_payload=None,
        editor_asset_files=None,
    )
    _write_bridge_info(
        tmp_path,
        token=_valid_token("cleanup"),
        session_id=materialized.session_id,
    )
    owned_info = _bridge_info_path(tmp_path)
    owned_source = game / f"zzrenforge_bridge_{materialized.session_id}.rpy"
    assert owned_source.exists()
    assert artifacts_path(tmp_path).exists()

    assert remove_bridge_artifacts(tmp_path, expected_session_id=materialized.session_id) is True

    assert not owned_source.exists()
    assert not owned_info.exists()
    assert not artifacts_path(tmp_path).exists()
    # Unowned / legacy paths preserved.
    assert legacy_inject.exists()
    assert (game / "renforge_bridge.rpyc").exists()
    assert legacy.read_text(encoding="utf-8") == '{"legacy":true}'
    assert (tmp_path / "traceback.txt").read_text(encoding="utf-8") == "boom"

    # Idempotent: a second call on an already-clean tree does not raise.
    assert remove_bridge_artifacts(tmp_path) is False


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
    (root / "traceback.txt").write_text("preexisting", encoding="utf-8")

    def fake_popen(*_args, **_kwargs):
        process = _FakeProcess()
        process.returncode = 1
        env = _kwargs["env"]
        session_id = env["RENFORGE_BRIDGE_SESSION_ID"]
        (root / "game" / f"zzrenforge_bridge_{session_id}.rpyc").write_bytes(b"compiled")
        _write_bridge_info(root, env)
        return process

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)

    with pytest.raises(RuntimeError, match="Game exited"):
        launch_with_bridge(sdk, project)

    assert not list((root / "game").glob("zzrenforge_bridge_*.rpy"))
    assert not list((root / "game").glob("zzrenforge_bridge_*.rpyc"))
    assert not (root / ".renforge" / "control" / "bridge.json").exists()
    assert not _artifacts_path(root).exists()
    # Unowned traceback is preserved (schema-3 cleanup never deletes it).
    assert (root / "traceback.txt").read_text(encoding="utf-8") == "preexisting"



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
        _write_bridge_info(project_root, _kwargs["env"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _LaggyClient())

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
        _write_bridge_info(project_root, _kwargs["env"])
        return process

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _WaitingClient())

    with pytest.raises(Exception) as excinfo:
        launch_with_bridge(
            sdk,
            project,
            startup_timeout=5.0,
            cancel_event=cancel_event,
        )

    assert getattr(excinfo.value, "code", None) == "LAUNCH_CANCELLED"
    assert process.terminated is True
    assert not list((project_root / "game").glob("zzrenforge_bridge_*.rpy"))
    assert not (project_root / ".renforge" / "control" / "bridge.json").exists()


def test_second_launch_same_project_fails_without_touching_first_session(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    popen_calls = {"count": 0}

    def fake_popen(*_args, **kwargs):
        popen_calls["count"] += 1
        _write_bridge_info(project_root, kwargs["env"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    first = launch_with_bridge(sdk, project, token=_valid_token("first-token"))
    injected_before = _owned_bridge_rpy(project_root).read_bytes()
    manifest_before = (project_root / ".renforge" / "control" / "bridge.json").read_bytes()

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, token=_valid_token("second-token"))

    assert getattr(excinfo.value, "code", None) == "BRIDGE_PROJECT_LOCKED"
    assert getattr(excinfo.value, "phase", None) == "acquiring_project_lock"
    assert popen_calls["count"] == 1
    assert _owned_bridge_rpy(project_root).read_bytes() == injected_before
    assert (project_root / ".renforge" / "control" / "bridge.json").read_bytes() == manifest_before

    first.close(timeout=0.1)
    assert (project_root / ".renforge" / "control" / "bridge.lock").exists()
    assert not (project_root / ".renforge" / "bridge.lock").exists()


def test_sessions_for_different_projects_are_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project_a, sdk, root_a = _make_project(tmp_path, "project-a")
    project_b, _, root_b = _make_project(tmp_path, "project-b")

    def fake_popen(command, env=None, **_kwargs):
        _write_bridge_info(Path(command[1]), env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    session_a = launch_with_bridge(sdk, project_a, token=_valid_token("token-a"))
    session_b = launch_with_bridge(sdk, project_b, token=_valid_token("token-b"))
    session_a.close(timeout=0.1)

    assert not list((root_a / "game").glob("zzrenforge_bridge_*.rpy"))
    assert not (root_a / ".renforge" / "control" / "bridge.json").exists()
    assert _owned_bridge_rpy(root_b).exists()
    assert json.loads((root_b / ".renforge" / "control" / "bridge.json").read_text())["token"] == _valid_token("token-b")

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
        _write_bridge_info(project_root, kwargs["env"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _CancellingClient())

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, cancel_event=cancel_event)
    assert getattr(excinfo.value, "code", None) == "LAUNCH_CANCELLED"

    cancel_event.clear()
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())
    session = launch_with_bridge(sdk, project)
    assert launches["count"] == 2
    session.close(timeout=0.1)


def test_bridge_manifest_with_wrong_token_is_never_accepted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    clock = iter((0.0, 0.0, 2.0))

    def fake_popen(*_args, **_kwargs):
        env = _kwargs["env"]
        # Same control path, foreign session id — readiness must reject it.
        _write_bridge_info(
            project_root,
            token=_valid_token("another-session-token"),
            session_id="ff" * 16,
        )
        assert env["RENFORGE_BRIDGE_SESSION_ID"] != "ff" * 16
        return _FakeProcess()

    def fail_client(_config):
        raise AssertionError("A manifest for another session must not create a client")

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", fail_client)
    monkeypatch.setattr("renforge.bridge.launcher.time.time", lambda: next(clock))
    monkeypatch.setattr("renforge.bridge.launcher.time.sleep", lambda _seconds: None)

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, token=_valid_token("expected-token"), startup_timeout=1.0)

    assert getattr(excinfo.value, "code", None) == "BRIDGE_CONNECTION_TIMEOUT"
    foreign = json.loads(_bridge_info_path(project_root).read_text(encoding="utf-8"))
    assert foreign["session_id"] == "ff" * 16


def test_allowlisted_bridge_startup_error_fails_before_timeout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)

    class _MarkerStream:
        def __init__(self, payload: bytes):
            self._payload = payload
            self._read = False

        def read(self, _size: int = -1):
            if self._read:
                return b""
            self._read = True
            return self._payload

    class _MarkerProcess(_FakeProcess):
        def __init__(self):
            super().__init__()
            self.stdout = _MarkerStream(b"")
            self.stderr = _MarkerStream(
                b"noise\nRENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT\n"
            )

    def fake_popen(*_args, **_kwargs):
        return _MarkerProcess()

    def fail_client(_config):
        raise AssertionError("startup marker must fail before client construction")

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", fail_client)
    monkeypatch.setattr("renforge.bridge.launcher.time.sleep", lambda _seconds: None)

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, startup_timeout=5.0)

    assert excinfo.value.code == "BRIDGE_INFO_CONFLICT"
    assert excinfo.value.phase == "waiting_for_bridge"
    assert not (project_root / ".renforge" / "control" / "bridge.json").exists()




def test_close_keeps_lock_and_artifacts_until_process_exit(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    resistant = _ResistantProcess()
    launches = {"count": 0}

    def fake_popen(*_args, **kwargs):
        launches["count"] += 1
        _write_bridge_info(project_root, kwargs["env"])
        return resistant if launches["count"] == 1 else _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    first = launch_with_bridge(sdk, project, token=_valid_token("first-token"))
    first_close = first.close(timeout=0.01)

    assert "process_alive" in first_close["failed"]
    assert first.closed is False
    assert _owned_bridge_rpy(project_root).exists()
    assert (project_root / ".renforge" / "control" / "bridge.json").exists()
    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, token=_valid_token("blocked-token"))
    assert excinfo.value.code == "BRIDGE_PROJECT_LOCKED"
    assert launches["count"] == 1

    resistant.exit()
    retry_close = first.close(timeout=0.01)
    assert retry_close.get("failed") is None
    assert first.closed is True
    assert not list((project_root / "game").glob("zzrenforge_bridge_*.rpy"))
    assert not (project_root / ".renforge" / "control" / "bridge.json").exists()

    second = launch_with_bridge(sdk, project, token=_valid_token("second-token"))
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
        _write_bridge_info(project_root, kwargs["env"])
        return process if launches["count"] == 1 else _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _CancellingClient())

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, cancel_event=cancel_event)

    assert excinfo.value.code == "LAUNCH_CANCELLED"
    assert process.terminated is True
    assert process.killed is True
    assert process.poll() == -9
    assert not list((project_root / "game").glob("zzrenforge_bridge_*.rpy"))

    cancel_event.clear()
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())
    session = launch_with_bridge(sdk, project)
    session.close(timeout=0.01)


def test_lock_file_open_permission_error_is_not_reported_as_contention(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    original_open = os.open

    def denied_open(path, flags, mode=0o777, *args, **kwargs):
        if Path(path).name == "bridge.lock":
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return original_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied_open)

    with pytest.raises(LaunchError) as excinfo:
        ProjectBridgeLock(project_root).acquire()

    assert excinfo.value.code == "BRIDGE_PROJECT_LOCK_FAILED"
    assert excinfo.value.phase == "acquiring_project_lock"


def test_lock_rejects_symlink_without_repair(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    control = project_root / ".renforge" / "control"
    control.mkdir(parents=True)
    os.chmod(control, 0o700)
    victim = tmp_path / "victim"
    victim.write_bytes(b"secret-bytes")
    lock_path = control / "bridge.lock"
    lock_path.symlink_to(victim)

    with pytest.raises(LaunchError) as excinfo:
        ProjectBridgeLock(project_root).acquire()

    assert excinfo.value.code == "BRIDGE_CONTROL_DIRECTORY_UNSAFE"
    assert excinfo.value.phase == "preparing_control_directory"
    assert victim.read_bytes() == b"secret-bytes"
    assert lock_path.is_symlink()
    assert not (project_root / ".renforge" / "bridge.lock").exists()


def test_preplanted_unsafe_bridge_info_fails_closed_without_unlink(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    control = project_root / ".renforge" / "control"
    control.mkdir(parents=True)
    if os.name != "nt":
        os.chmod(control, 0o700)
    victim = tmp_path / "victim-bridge.json"
    victim.write_bytes(b"secret-bridge-bytes")
    unsafe = control / "bridge.json"
    unsafe.symlink_to(victim)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("Popen must not run when bridge metadata is unsafe")

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fail_popen)

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project)

    assert excinfo.value.code == "BRIDGE_INFO_CONFLICT"
    assert unsafe.is_symlink()
    assert victim.read_bytes() == b"secret-bridge-bytes"
    assert not list((project_root / "game").glob("zzrenforge_bridge_*.rpy"))
    assert not _artifacts_path(project_root).exists()

    assert remove_bridge_artifacts(project_root) is False
    assert unsafe.is_symlink()
    assert victim.read_bytes() == b"secret-bridge-bytes"



def test_lock_rejects_world_writable_control_dir(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    control = project_root / ".renforge" / "control"
    control.mkdir(parents=True)
    os.chmod(control, 0o777)

    with pytest.raises(LaunchError) as excinfo:
        ProjectBridgeLock(project_root).acquire()

    assert excinfo.value.code == "BRIDGE_CONTROL_DIRECTORY_UNSAFE"
    assert excinfo.value.phase == "preparing_control_directory"
    assert not (control / "bridge.lock").exists()


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
        _write_bridge_info(project_root, kwargs["env"])
        return process if launches["count"] == 1 else _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _CancellingClient())

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, cancel_event=cancel_event)
    assert excinfo.value.code == "LAUNCH_CANCELLED"
    assert _owned_bridge_rpy(project_root).exists()

    with pytest.raises(LaunchError) as locked:
        launch_with_bridge(sdk, project)
    assert locked.value.code == "BRIDGE_PROJECT_LOCKED"
    assert launches["count"] == 1

    process.exit()
    cancel_event.clear()
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())
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


def test_launch_without_editor_does_not_start_editor_flow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    for key in ("RENFORGE_EDITOR_HOST", "RENFORGE_EDITOR_PORT", "RENFORGE_EDITOR_TOKEN", "RENFORGE_EDITOR_PROTOCOL"):
        monkeypatch.delenv(key, raising=False)
    project, sdk, project_root = _make_project(tmp_path)
    captured: dict[str, Any] = {}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        captured["env"] = env
        assert env is not None
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())
    monkeypatch.setattr(
        "renforge.bridge.launcher.EditorCoordinator",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("editor flow started")),
    )

    session = launch_with_bridge(sdk, project, editor=False)
    assert session is not None
    assert not session.editor
    env = captured.get("env") or {}
    assert not any(key.startswith("RENFORGE_EDITOR_") for key in env)
    assert session.editor is False
    manifest = _load_artifacts(project_root)
    assert [entry["role"] for entry in manifest["sources"]] == ["bridge"]
    assert manifest["asset_tree"] is None
    assert not list((project_root / "game").glob("zzrenforge_editor_*"))
    session.close(timeout=0.1)


def test_launch_with_editor_passes_exact_editor_environment_and_owned_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    captured: dict[str, Any] = {}
    probe: dict[str, object] = {}
    coordinator: dict[str, object] = {}
    close_calls: dict[str, int] = {"count": 0}

    class _Coordinator:
        def __init__(self, _project: RenpyProject, _sdk: RenpySdk):
            coordinator["init"] = True

        def attach_runtime_probe(self, runtime_probe: object) -> None:
            probe["value"] = runtime_probe

        def start(self) -> EditorEndpoint:
            return EditorEndpoint(host="127.0.0.1", port=51234, token="editor-token", protocol_version=PROTOCOL_VERSION)

        def close(self, timeout: float = 10.0) -> dict[str, Any]:
            close_calls["count"] += 1
            return {"closed": True}

    class _Probe:
        def __init__(self, project_root: str | Path):
            probe["project_root"] = str(project_root)

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        captured["env"] = env
        assert env is not None
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.EditorCoordinator", _Coordinator)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeRuntimeProbe", _Probe)
    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    session = launch_with_bridge(sdk, project, editor=True)
    assert coordinator.get("init") is True
    assert session.editor is True
    env = captured["env"]
    editor_keys = {k for k in env if k.startswith("RENFORGE_EDITOR_")}
    assert editor_keys == {
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
        "RENFORGE_EDITOR_ASSETS",
        "RENFORGE_EDITOR_LANG",
        "RENFORGE_EDITOR_FONT",
    }
    assert env["RENFORGE_EDITOR_HOST"] == "127.0.0.1"
    assert env["RENFORGE_EDITOR_PORT"] == "51234"
    assert env["RENFORGE_EDITOR_TOKEN"] == "editor-token"
    assert env["RENFORGE_EDITOR_PROTOCOL"] == "1"
    assert env["RENFORGE_EDITOR_LANG"] == "en"
    assert probe["project_root"] == str(project_root)
    assert isinstance(probe["value"], _Probe)

    from renforge.bridge.launcher import _editor_payload

    manifest = _load_artifacts(project_root)
    assert manifest["schema_version"] == 3
    editor_source = next(entry for entry in manifest["sources"] if entry["role"] == "editor")
    basename = editor_source["basename"]
    source_path = project_root / "game" / basename
    expected_bytes = _editor_payload()
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    assert editor_source["sha256"] == expected_hash
    assert source_path.read_bytes() == expected_bytes

    asset_tree = manifest["asset_tree"]
    assert asset_tree is not None
    assert asset_tree["dirname"] == basename[: -len(".rpy")]
    assert env["RENFORGE_EDITOR_ASSETS"] == asset_tree["dirname"]
    assert env["RENFORGE_BRIDGE_SESSION_ID"] == manifest["session_id"]
    for entry in asset_tree["files"]:
        shipped = project_root / "game" / asset_tree["dirname"] / entry["path"]
        assert hashlib.sha256(shipped.read_bytes()).hexdigest() == entry["sha256"]

    close_result = session.close(timeout=0.1)
    assert close_result["cleaned"]["editor_coordinator"] is True
    assert close_result["cleaned"]["bridge_artifacts"] is True
    assert close_calls["count"] == 1
    assert session.closed is True


def test_launch_with_editor_retries_collision_free_session_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    session_ids = iter(["deadbeefdeadbeefdeadbeefdeadbeef", "cafebabecafebabecafebabecafebabe"])

    def _token_hex(size: int = 32) -> str:
        if size == 16:
            return next(session_ids)
        return "ab" * size

    monkeypatch.setattr("renforge.bridge.artifacts.secrets.token_hex", _token_hex)
    collision = "zzrenforge_bridge_deadbeefdeadbeefdeadbeefdeadbeef.rpy"
    (project.game_dir / collision).write_text("occupied", encoding="utf-8")
    (project.game_dir / f"{collision}c").write_bytes(b"compiled")
    (project.game_dir / f"{collision}c.bak").write_bytes(b"compiled")

    class _Coordinator:
        def __init__(self, _project: RenpyProject, _sdk: RenpySdk):
            pass

        def attach_runtime_probe(self, _probe: object) -> None:
            return None

        def start(self) -> EditorEndpoint:
            return EditorEndpoint(host="127.0.0.1", port=62010, token="editor-token", protocol_version=PROTOCOL_VERSION)

        def close(self, timeout: float = 10.0) -> dict[str, Any]:
            return {"closed": True}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        assert env is not None
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.EditorCoordinator", _Coordinator)
    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    session = launch_with_bridge(sdk, project, editor=True)
    manifest = _load_artifacts(project_root)
    assert manifest["session_id"] == "cafebabecafebabecafebabecafebabe"
    assert (project.game_dir / collision).read_text(encoding="utf-8") == "occupied"
    assert (project.game_dir / "zzrenforge_bridge_cafebabecafebabecafebabecafebabe.rpy").exists()
    session.close(timeout=0.1)


def test_editor_close_preserves_modified_injected_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)

    class _Coordinator:
        def __init__(self, _project: RenpyProject, _sdk: RenpySdk):
            pass

        def attach_runtime_probe(self, _probe: object) -> None:
            return None

        def start(self) -> EditorEndpoint:
            return EditorEndpoint(host="127.0.0.1", port=63333, token="editor-token", protocol_version=PROTOCOL_VERSION)

        def close(self, timeout: float = 10.0) -> dict[str, Any]:
            return {"closed": True}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.EditorCoordinator", _Coordinator)
    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    session = launch_with_bridge(sdk, project, editor=True)
    manifest = _load_artifacts(project_root)
    editor_source = next(entry for entry in manifest["sources"] if entry["role"] == "editor")
    injected = project_root / "game" / editor_source["basename"]
    sibling = project_root / "game" / f"{editor_source['basename']}c"
    injected.write_text("modified-by-test", encoding="utf-8")
    sibling.write_bytes(b"foreign")

    close_result = session.close(timeout=0.1)
    assert "bridge_artifacts" in close_result["failed"]
    assert session.closed is False
    assert injected.exists()
    assert sibling.exists()
    assert _artifacts_path(project_root).exists()
    assert "renpy_process" in close_result["cleaned"]


def test_bridge_session_close_is_idempotent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    process = _FakeProcess()
    session = BridgeSession(process, _FakeClient(), project_root)

    first = session.close(timeout=0.1)
    second = session.close(timeout=0.1)

    assert session.closed is True
    assert first == second
    assert process.killed is True
    assert second["cleaned"]["renpy_process"] is True


def test_editor_launch_failure_cleans_resources_and_closes_coordinator(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    coordinator_calls = {"close": 0, "init": 0, "start": 0}
    process = _FakeProcess()

    class _Coordinator:
        def __init__(self, _project: RenpyProject, _sdk: RenpySdk):
            coordinator_calls["init"] += 1

        def attach_runtime_probe(self, _probe: object) -> None:
            return None

        def start(self) -> EditorEndpoint:
            coordinator_calls["start"] += 1
            return EditorEndpoint(host="127.0.0.1", port=62020, token="editor-token", protocol_version=PROTOCOL_VERSION)

        def close(self, timeout: float = 10.0) -> dict[str, Any]:
            coordinator_calls["close"] += 1
            return {"closed": True}

    def fake_popen(*_args, **_kwargs):
        return process

    monkeypatch.setattr("renforge.bridge.launcher.EditorCoordinator", _Coordinator)
    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    with pytest.raises(LaunchError) as excinfo:
        launch_with_bridge(sdk, project, editor=True, startup_timeout=0.0)

    assert excinfo.value.code == "BRIDGE_CONNECTION_TIMEOUT"
    assert coordinator_calls["init"] == 1
    assert coordinator_calls["start"] == 1
    assert coordinator_calls["close"] == 1
    assert not _artifacts_path(project_root).exists()
    assert process.returncode is not None


def test_editor_artifact_cleanup_stays_retryable_after_any_unlink_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """Cleanup must be idempotent at every unlink.

    ``BridgeSession.close()`` and the deferred reaper both retry removal, and a
    failure that leaves the manifest pointing at an already-deleted artifact
    would make every later attempt abort before reaching the artifact that
    actually failed — stranding the project lock forever.
    """
    from renforge.bridge.artifacts import allocate_and_materialize, remove_owned_artifacts
    from renforge.project import RenpyProject

    game_dir = tmp_path / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    project = RenpyProject(tmp_path)
    real_unlink = Path.unlink

    for fail_kind in ("bridge", "editor", "sibling", "manifest"):
        materialized = allocate_and_materialize(
            project,
            bridge_payload=b"init python:\n    pass\n",
            include_session_init=False,
            editor_payload=b"screen _renforge_editor_launcher():\n    pass\n",
            editor_asset_files=[],
        )
        session_id = materialized.session_id
        bridge_path = game_dir / f"zzrenforge_bridge_{session_id}.rpy"
        editor_path = game_dir / f"zzrenforge_editor_{session_id}.rpy"
        sibling = game_dir / f"zzrenforge_editor_{session_id}.rpyc"
        sibling.write_bytes(b"compiled")
        manifest_path = _artifacts_path(tmp_path)
        failing = {
            "bridge": bridge_path,
            "editor": editor_path,
            "sibling": sibling,
            "manifest": manifest_path,
        }[fail_kind]

        def flaky_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
            if self == failing or self.name == failing.name:
                raise OSError(errno.EACCES, "artifact is locked")
            real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        with pytest.raises((OSError, RuntimeError)):
            remove_owned_artifacts(tmp_path, expected_session_id=session_id)
        monkeypatch.setattr(Path, "unlink", real_unlink)

        remove_owned_artifacts(tmp_path, expected_session_id=session_id)
        assert not bridge_path.exists()
        assert not editor_path.exists()
        assert not sibling.exists()
        assert not manifest_path.exists()


def test_editor_artifact_cleanup_validates_every_digest_before_unlink(tmp_path: Path) -> None:
    from renforge.bridge.artifacts import (
        ArtifactOwnershipError,
        allocate_and_materialize,
        remove_owned_artifacts,
    )

    game_dir = tmp_path / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    project = RenpyProject(tmp_path)
    materialized = allocate_and_materialize(
        project,
        bridge_payload=b"init python:\n    pass\n",
        include_session_init=False,
        editor_payload=b"screen _renforge_editor_launcher():\n    pass\n",
        editor_asset_files=[],
    )
    bridge_path = game_dir / f"zzrenforge_bridge_{materialized.session_id}.rpy"
    editor_path = game_dir / f"zzrenforge_editor_{materialized.session_id}.rpy"
    original_bridge = bridge_path.read_bytes()
    editor_path.write_bytes(b"changed after publication\n")

    with pytest.raises(ArtifactOwnershipError) as excinfo:
        remove_owned_artifacts(
            tmp_path,
            expected_session_id=materialized.session_id,
            remove_bridge_info=False,
        )

    assert excinfo.value.code == "BRIDGE_ARTIFACT_OWNERSHIP_CONFLICT"
    assert bridge_path.read_bytes() == original_bridge
    assert editor_path.read_bytes() == b"changed after publication\n"
    assert _artifacts_path(tmp_path).exists()


def test_editor_artifact_cleanup_refuses_dangling_symlink_artifacts(tmp_path: Path) -> None:
    """A dangling symlink must never read as an absent owned artifact."""
    from renforge.bridge.artifacts import allocate_and_materialize, remove_owned_artifacts
    from renforge.project import RenpyProject

    game_dir = tmp_path / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    project = RenpyProject(tmp_path)

    for fail_kind in ("editor", "sibling", "manifest"):
        materialized = allocate_and_materialize(
            project,
            bridge_payload=b"init python:\n    pass\n",
            include_session_init=False,
            editor_payload=b"screen _renforge_editor_launcher():\n    pass\n",
            editor_asset_files=[],
        )
        session_id = materialized.session_id
        editor_path = game_dir / f"zzrenforge_editor_{session_id}.rpy"
        sibling = game_dir / f"zzrenforge_editor_{session_id}.rpyc"
        sibling.write_bytes(b"compiled")
        manifest_path = _artifacts_path(tmp_path)
        target = {
            "editor": editor_path,
            "sibling": sibling,
            "manifest": manifest_path,
        }[fail_kind]

        target.unlink()
        try:
            target.symlink_to(game_dir / "nowhere")
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            pytest.skip(f"symlinks unavailable on this platform: {exc}")

        assert target.is_symlink() and not target.exists(), str(target)
        with pytest.raises((RuntimeError, Exception)):
            remove_owned_artifacts(tmp_path, expected_session_id=session_id)
        assert target.is_symlink(), str(target)
        # Clean for the next iteration when possible (manifest may be the symlink).
        if fail_kind != "manifest":
            target.unlink()
            # restore a real file so full cleanup can succeed
            if fail_kind == "editor":
                editor_path.write_bytes(b"screen _renforge_editor_launcher():\n    pass\n")
            remove_owned_artifacts(tmp_path, expected_session_id=session_id)
        else:
            target.unlink()
            # Without a valid manifest, leftover owned sources remain; wipe game injects.
            for leftover in game_dir.glob("zzrenforge_*"):
                if leftover.is_symlink() or leftover.is_file():
                    leftover.unlink()
            for leftover in (game_dir / f"zzrenforge_editor_{session_id}").glob("*") if False else []:
                pass


def test_shutdown_incomplete_keeps_session_lock_until_coordinator_close_retries(
    monkeypatch, tmp_path: Path
) -> None:
    """The Codex P1 contract lives at the BridgeSession boundary.

    A coordinator that reports an incomplete shutdown — a handler can still reach
    ``atomic_write_file`` — must leave ``session.closed`` False and the project
    lock held, so no concurrent session can launch over the half-torn-down one.
    `_close_resources` keeps killing Ren'Py and removing artifacts after the
    exception; the lock is the real safeguard, released only once a retry sees a
    clean close.
    """
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, project_root = _make_project(tmp_path)
    close_calls = {"count": 0}

    class _StalledCoordinator:
        def __init__(self, _project: RenpyProject, _sdk: RenpySdk):
            pass

        def attach_runtime_probe(self, _probe: object) -> None:
            return None

        def start(self) -> EditorEndpoint:
            return EditorEndpoint(host="127.0.0.1", port=64444, token="editor-token", protocol_version=PROTOCOL_VERSION)

        def close(self, timeout: float = 10.0) -> dict[str, Any]:
            close_calls["count"] += 1
            if close_calls["count"] == 1:
                raise EditorError("SHUTDOWN_INCOMPLETE", "handler still running", {"active_commands": 1})
            return {"closed": True}

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        _write_bridge_info(project_root, env)
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.EditorCoordinator", _StalledCoordinator)
    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient", lambda _config: _FakeClient())

    session = launch_with_bridge(sdk, project, editor=True)

    first = session.close(timeout=0.1)
    assert "editor_coordinator" in first["failed"]
    assert session.closed is False

    # The project lock is the real safeguard: a second session must not launch.
    with pytest.raises(LaunchError) as excinfo:
        ProjectBridgeLock(project_root).acquire()
    assert excinfo.value.code == "BRIDGE_PROJECT_LOCKED"

    # The retry joins the now-finished handler and releases everything.
    second = session.close(timeout=0.1)
    assert second.get("failed", []) == []
    assert session.closed is True

    # With the lock released, a fresh session can take ownership again.
    reacquired = ProjectBridgeLock(project_root)
    reacquired.acquire()
    try:
        pass
    finally:
        reacquired.release()



def test_editor_language_falls_back_to_english_for_anything_unsupported(monkeypatch) -> None:
    from renforge.bridge.launcher import _editor_language

    monkeypatch.setenv("RENFORGE_LANG", "zh-CN")
    assert _editor_language() == "zh-CN"

    # Whitespace is operator error, not a language.
    monkeypatch.setenv("RENFORGE_LANG", "  ")
    assert _editor_language() == "en"

    # An unshipped locale must not reach the overlay: it would render every
    # label as its own key rather than as text.
    monkeypatch.setenv("RENFORGE_LANG", "fr")
    assert _editor_language() == "en"

    monkeypatch.delenv("RENFORGE_LANG", raising=False)
    assert _editor_language() == "en"


def test_editor_asset_sources_ship_both_locale_catalogues() -> None:
    from renforge.bridge.launcher import _editor_asset_sources

    shipped = {relative for relative, _source in _editor_asset_sources()}
    # en.json is what keeps the live suites green: it must reproduce the
    # literals the runners assert on, so it can never go missing silently.
    assert "locales/en.json" in shipped
    assert "locales/zh-CN.json" in shipped


def test_editor_locale_catalogues_have_identical_keys() -> None:
    import json as _json
    from renforge.bridge.launcher import _EDITOR_ASSETS_RESOURCE

    locales = _EDITOR_ASSETS_RESOURCE / "locales"
    english = _json.loads((locales / "en.json").read_text(encoding="utf-8"))
    chinese = _json.loads((locales / "zh-CN.json").read_text(encoding="utf-8"))
    # Same parity rule the dashboard enforces at build time.
    assert sorted(english) == sorted(chinese)
    assert all(value.strip() for value in english.values())
    assert all(value.strip() for value in chinese.values())


def test_editor_builtin_english_matches_the_shipped_catalogue() -> None:
    """The .rpy carries English so a missing catalogue degrades to readable text.

    Those built-ins and en.json must agree, or the editor would render one set
    of labels when assets ship and a different one when they do not.
    """
    import ast
    import json as _json
    from renforge.bridge.launcher import _EDITOR_ASSETS_RESOURCE, _EDITOR_RESOURCE

    source = _EDITOR_RESOURCE.read_text(encoding="utf-8")
    start = source.index("_RF_UI_STRINGS = {")
    literal = source[start + len("_RF_UI_STRINGS = ") :]
    depth, end = 0, None
    for index, char in enumerate(literal):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None
    builtins_map = ast.literal_eval(literal[:end])

    english = _json.loads(
        (_EDITOR_ASSETS_RESOURCE / "locales" / "en.json").read_text(encoding="utf-8")
    )
    assert builtins_map == english


def test_editor_falls_back_to_english_when_no_cjk_font_exists(monkeypatch) -> None:
    """Chinese without a font renders as empty boxes, which is worse than English."""
    from renforge.bridge import launcher

    endpoint = EditorEndpoint(
        host="127.0.0.1", port=1, token="t", protocol_version=PROTOCOL_VERSION
    )
    monkeypatch.setenv("RENFORGE_LANG", "zh-CN")

    stranded = launcher._editor_environment(endpoint, assets_dirname="d", font_relative="")
    assert stranded["RENFORGE_EDITOR_LANG"] == "en"
    assert stranded["RENFORGE_EDITOR_FONT"] == ""

    served = launcher._editor_environment(
        endpoint, assets_dirname="d", font_relative="fonts/cjk.ttc"
    )
    assert served["RENFORGE_EDITOR_LANG"] == "zh-CN"
    assert served["RENFORGE_EDITOR_FONT"] == "fonts/cjk.ttc"


def test_editor_font_is_only_borrowed_for_languages_that_need_one(monkeypatch, tmp_path) -> None:
    """A 20 MB copy into the user's game directory needs an actual reason."""
    from renforge.bridge import launcher

    source = tmp_path / "fake-cjk.ttc"
    source.write_bytes(b"not really a font, but bytes all the same")
    monkeypatch.setattr(launcher, "_editor_font_candidates", lambda: (source,))
    monkeypatch.setattr(launcher, "_editor_asset_sources", lambda: [])

    monkeypatch.setenv("RENFORGE_LANG", "en")
    files, font_rel = launcher._prepare_editor_asset_payloads()
    assert font_rel == ""
    assert files == []

    monkeypatch.setenv("RENFORGE_LANG", "zh-CN")
    files, font_rel = launcher._prepare_editor_asset_payloads()
    assert font_rel == "fonts/cjk.ttc"
    assert files == [("fonts/cjk.ttc", source.read_bytes())]


def test_editor_font_absent_from_the_repository() -> None:
    """No font ships here: the interface borrows one from the host machine."""
    from renforge.bridge.launcher import _editor_asset_sources

    shipped = {relative for relative, _source in _editor_asset_sources()}
    assert not any(name.startswith("fonts/") for name in shipped)


def _load_lock_classifier():
    """Pull the classifier out of the .rpy so plain pytest can exercise it.

    It is deliberately dependency-free — a dict and string suffixes — precisely
    so the rule can be checked without booting Ren'Py.
    """
    import textwrap
    from renforge.bridge.launcher import _EDITOR_RESOURCE

    source = _EDITOR_RESOURCE.read_text(encoding="utf-8")
    start = source.index("    _RF_LOCK_EXPLICIT = {")
    end = source.index("    def _renforge_editor_lock_message(")
    namespace: dict[str, object] = {}
    exec(textwrap.dedent(source[start:end]), namespace)
    return namespace["_renforge_editor_lock_level"]


def test_every_real_lock_code_lands_in_a_level() -> None:
    """No refusal may be silent: an unclassified code would show nothing at all."""
    import re
    from pathlib import Path as _Path

    classify = _load_lock_classifier()
    root = _Path(__file__).resolve().parents[1] / "src" / "renforge" / "editor"
    codes: set[str] = set()
    for name in ("coordinator.py", "source.py"):
        text = (root / name).read_text(encoding="utf-8")
        codes.update(re.findall(r'_lock_reason\(\s*"([A-Z_]{4,})"', text))
        codes.update(re.findall(r'Error\(\s*"([A-Z_]{4,})"', text))

    assert codes, "expected to find lock codes in the editor sources"
    for code in codes:
        assert classify(code) in {"locked", "blocked", "refused"}, code


def test_lock_levels_say_only_what_was_measured() -> None:
    classify = _load_lock_classifier()

    # A source form that can never be edited in place.
    assert classify("MULTILINE_STATEMENT_REJECTED") == "locked"
    assert classify("CONTAINER_POSITION_UNSUPPORTED") == "locked"
    assert classify("ID_LITERAL_REQUIRED") == "locked"

    # Editable in principle, unproven on this instance.
    assert classify("ANCESTRY_TYPE_UNPROVEN") == "blocked"
    assert classify("RUNTIME_KEY_INVALID") == "blocked"

    # Attempted, rejected, rolled back.
    assert classify("ID_MISMATCH") == "refused"
    assert classify("ATTESTATION_FAILED") == "refused"
    assert classify("ANALYSIS_STALE_GENERATION") == "refused"

    # An unknown code must not claim a capability boundary nobody measured.
    assert classify("SOME_CODE_ADDED_NEXT_YEAR") == "blocked"
    assert classify(None) is None
    assert classify("") is None


def test_injected_payload_is_the_sum_of_every_source_file() -> None:
    """One artifact, many sources — that split is what lets panels be written in parallel."""
    from renforge.bridge.launcher import (
        _EDITOR_RESOURCE,
        _editor_payload,
        _editor_screen_sources,
    )

    screens = _editor_screen_sources()
    assert screens, "expected the region screens to live in their own files"
    assert {path.name for path in screens} >= {"rf_toolbar.rpy", "rf_tree.rpy"}

    payload = _editor_payload()
    assert payload.startswith(_EDITOR_RESOURCE.read_bytes())
    for path in screens:
        assert path.read_bytes() in payload

    # Order must not drift with the filesystem, or the digest changes for no reason.
    assert [path.name for path in screens] == sorted(path.name for path in screens)


def test_region_screens_are_not_duplicated_in_the_core_file() -> None:
    """A screen defined twice would silently shadow itself after a refactor."""
    from renforge.bridge.launcher import _EDITOR_RESOURCE, _editor_screen_sources

    core = _EDITOR_RESOURCE.read_text(encoding="utf-8")
    for path in _editor_screen_sources():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("screen "):
                assert line not in core, f"{line!r} defined in both {path.name} and editor.rpy"


def test_analysis_in_flight_is_not_reported_as_a_refusal() -> None:
    """ANALYZING shares the lock field but means "still deciding", not "no".

    Announcing a refusal for the half-second an analysis takes would teach the
    user to distrust the word when it matters.
    """
    classify = _load_lock_classifier()
    assert classify("ANALYZING") is None
    # Genuinely unmeasured is still a refusal to act, and must stay visible.
    assert classify("UNMEASURED") == "blocked"
    assert classify("AMBIGUOUS_HIT") == "locked"


def test_no_widget_id_is_claimed_by_two_region_screens() -> None:
    """Two widgets sharing an id make renpy.get_widget return an arbitrary one.

    Panels are written independently, so nothing but a test stops a new one from
    reusing a name the toolbar already owns.
    """
    import re
    from collections import Counter
    from renforge.bridge.launcher import _EDITOR_RESOURCE, _editor_screen_sources

    owners: Counter[str] = Counter()
    for path in [_EDITOR_RESOURCE, *_editor_screen_sources()]:
        found = set(re.findall(r'^\s*id\s+"(rf_[a-z_]+)"', path.read_text(encoding="utf-8"), re.M))
        owners.update(found)

    duplicated = sorted(name for name, count in owners.items() if count > 1)
    assert not duplicated, f"widget ids claimed by more than one file: {duplicated}"


def test_private_directory_create_and_validate(tmp_path: Path) -> None:
    from renforge.util.files import ensure_private_directory

    control = tmp_path / ".renforge" / "control"
    created = ensure_private_directory(control)
    assert created == (control if control.is_absolute() else control.absolute())
    assert created.is_dir()
    st = created.lstat()
    assert not stat.S_ISLNK(st.st_mode)
    assert stat.S_ISDIR(st.st_mode)
    if os.name != "nt":
        assert st.st_uid == os.geteuid()
        assert (st.st_mode & 0o777) == 0o700
    # Idempotent on an already-private directory.
    assert ensure_private_directory(created) == created


def test_private_directory_rejects_symlink_without_touching_victim(tmp_path: Path) -> None:
    from renforge.util.files import PrivatePathError, ensure_private_directory

    victim = tmp_path / "victim"
    victim.mkdir()
    marker = victim / "secret.txt"
    marker.write_text("keep-me", encoding="utf-8")
    link = tmp_path / "control-link"
    link.symlink_to(victim, target_is_directory=True)

    with pytest.raises(PrivatePathError) as exc_info:
        ensure_private_directory(link)
    assert exc_info.value.code == "PRIVATE_DIRECTORY_UNSAFE"
    assert marker.read_text(encoding="utf-8") == "keep-me"
    assert victim.is_dir()


def test_private_directory_rejects_ancestor_symlink_without_touching_victim(
    tmp_path: Path,
) -> None:
    from renforge.util.files import PrivatePathError, ensure_private_directory

    # project/.renforge is a symlink outside the project; control under it must fail closed.
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "secret.txt"
    marker.write_text("keep-me", encoding="utf-8")
    renforge_link = project / ".renforge"
    renforge_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PrivatePathError) as exc_info:
        ensure_private_directory(project / ".renforge" / "control")
    assert exc_info.value.code == "PRIVATE_DIRECTORY_UNSAFE"
    assert marker.read_text(encoding="utf-8") == "keep-me"
    assert not (outside / "control").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_private_directory_rejects_wrong_mode_without_repair(tmp_path: Path) -> None:
    from renforge.util.files import PrivatePathError, ensure_private_directory

    bad = tmp_path / "control"
    bad.mkdir()
    os.chmod(bad, 0o755)
    with pytest.raises(PrivatePathError) as exc_info:
        ensure_private_directory(bad)
    assert exc_info.value.code == "PRIVATE_DIRECTORY_UNSAFE"
    assert (bad.lstat().st_mode & 0o777) == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_private_directory_rejects_non_directory(tmp_path: Path) -> None:
    from renforge.util.files import PrivatePathError, ensure_private_directory

    path = tmp_path / "not-a-dir"
    path.write_text("x", encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(PrivatePathError) as exc_info:
        ensure_private_directory(path)
    assert exc_info.value.code == "PRIVATE_DIRECTORY_UNSAFE"
    assert path.read_text(encoding="utf-8") == "x"


def test_atomic_write_private_json_and_nofollow_read(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        atomic_write_private_json,
        ensure_private_directory,
        read_regular_file_nofollow,
    )

    control = ensure_private_directory(tmp_path / "control")
    path = control / "bridge.json"
    payload = {"schema_version": 1, "state": "starting", "host": "127.0.0.1"}
    atomic_write_private_json(path, payload, max_bytes=16 * 1024)

    st = path.lstat()
    assert stat.S_ISREG(st.st_mode)
    if os.name != "nt":
        assert st.st_uid == os.geteuid()
        assert (st.st_mode & 0o777) == 0o600

    raw = read_regular_file_nofollow(path, max_bytes=16 * 1024)
    assert json.loads(raw.decode("utf-8")) == payload

    # Overwrite stays private and replaces contents atomically.
    payload2 = {"schema_version": 1, "state": "ready", "port": 9}
    atomic_write_private_json(path, payload2, max_bytes=16 * 1024)
    assert json.loads(read_regular_file_nofollow(path, max_bytes=16 * 1024)) == payload2


def test_atomic_write_private_json_rejects_symlink_preserves_victim(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        atomic_write_private_json,
        ensure_private_directory,
    )

    control = ensure_private_directory(tmp_path / "control")
    victim = tmp_path / "victim.json"
    victim.write_text('{"keep":true}', encoding="utf-8")
    link = control / "bridge.json"
    link.symlink_to(victim)

    with pytest.raises(PrivatePathError) as exc_info:
        atomic_write_private_json(link, {"state": "ready"}, max_bytes=1024)
    assert exc_info.value.code == "PRIVATE_FILE_UNSAFE"
    assert victim.read_text(encoding="utf-8") == '{"keep":true}'
    assert link.is_symlink()


def test_read_regular_file_nofollow_rejects_symlink_preserves_victim(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        ensure_private_directory,
        read_regular_file_nofollow,
    )

    control = ensure_private_directory(tmp_path / "control")
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"secret-bytes")
    link = control / "bridge.json"
    link.symlink_to(victim)

    with pytest.raises(PrivatePathError) as exc_info:
        read_regular_file_nofollow(link, max_bytes=1024)
    assert exc_info.value.code == "PRIVATE_FILE_UNSAFE"
    assert victim.read_bytes() == b"secret-bytes"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_read_regular_file_nofollow_rejects_wrong_mode_without_repair(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        atomic_write_private_json,
        ensure_private_directory,
        read_regular_file_nofollow,
    )

    control = ensure_private_directory(tmp_path / "control")
    path = control / "bridge.json"
    atomic_write_private_json(path, {"ok": True}, max_bytes=1024)
    os.chmod(path, 0o644)
    with pytest.raises(PrivatePathError) as exc_info:
        read_regular_file_nofollow(path, max_bytes=1024)
    assert exc_info.value.code == "PRIVATE_FILE_UNSAFE"
    assert (path.lstat().st_mode & 0o777) == 0o644
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_atomic_write_private_json_rejects_wrong_existing_mode(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        atomic_write_private_json,
        ensure_private_directory,
    )

    control = ensure_private_directory(tmp_path / "control")
    path = control / "bridge.json"
    path.write_text('{"old":1}', encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(PrivatePathError) as exc_info:
        atomic_write_private_json(path, {"new": 2}, max_bytes=1024)
    assert exc_info.value.code == "PRIVATE_FILE_UNSAFE"
    assert path.read_text(encoding="utf-8") == '{"old":1}'
    assert (path.lstat().st_mode & 0o777) == 0o644


def test_private_json_and_read_enforce_max_bytes(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        atomic_write_private_json,
        ensure_private_directory,
        read_regular_file_nofollow,
    )

    control = ensure_private_directory(tmp_path / "control")
    path = control / "bridge.json"
    atomic_write_private_json(path, {"v": 1}, max_bytes=1024)
    original = path.read_bytes()

    with pytest.raises(PrivatePathError) as write_exc:
        atomic_write_private_json(path, {"blob": "x" * 200}, max_bytes=32)
    assert write_exc.value.code == "PRIVATE_FILE_UNSAFE"
    assert path.read_bytes() == original

    # Force a too-large private file and ensure read rejects without truncating.
    huge = b"{" + b"a" * 200 + b"}"
    if os.name != "nt":
        fd = os.open(str(path), os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, huge)
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    else:
        path.write_bytes(huge)

    with pytest.raises(PrivatePathError) as read_exc:
        read_regular_file_nofollow(path, max_bytes=64)
    assert read_exc.value.code == "PRIVATE_FILE_UNSAFE"
    assert path.read_bytes() == huge


@pytest.mark.skipif(os.name == "nt", reason="POSIX file type check")
def test_read_regular_file_nofollow_rejects_directory(tmp_path: Path) -> None:
    from renforge.util.files import (
        PrivatePathError,
        ensure_private_directory,
        read_regular_file_nofollow,
    )

    control = ensure_private_directory(tmp_path / "control")
    with pytest.raises(PrivatePathError) as exc_info:
        read_regular_file_nofollow(control, max_bytes=16)
    assert exc_info.value.code == "PRIVATE_FILE_UNSAFE"


def test_existing_public_atomic_writers_remain_compatible(tmp_path: Path) -> None:
    from renforge.util.files import write_atomic, write_json_atomic

    text_path = tmp_path / "note.txt"
    write_atomic(text_path, "hello")
    assert text_path.read_text(encoding="utf-8") == "hello"

    json_path = tmp_path / "data.json"
    write_json_atomic(json_path, {"ok": True}, follow_symlinks=False, max_bytes=1024)
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"ok": True}


def test_windows_project_bridge_lock_helper_wiring_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    import io
    import sys
    import types
    from renforge.bridge.launcher import ProjectBridgeLock, _LockPathUnsafe
    from renforge.util import files as private_files

    called = {"is_reparse": False, "set_dacl": False, "val_dacl": False, "closed_file": False}

    def fake_win_is_reparse(path):
        called["is_reparse"] = True
        return False

    def fake_win_set_dacl(path):
        called["set_dacl"] = True

    def fake_win_val_dacl(path):
        called["val_dacl"] = True
        raise RuntimeError("DACL check failed")

    monkeypatch.setattr("renforge.util.files._win_is_reparse", fake_win_is_reparse)
    monkeypatch.setattr("renforge.util.files._win_set_protected_dacl", fake_win_set_dacl)
    monkeypatch.setattr("renforge.util.files._win_validate_protected_dacl", fake_win_val_dacl)

    fake_h = 100

    class FakeCreateFileW:
        restype = None
        argtypes = None

        def __call__(self, *args):
            return fake_h

    class FakeKernel32:
        CreateFileW = FakeCreateFileW()

        def GetFileType(self, handle):
            return 0x0001

        def GetFileInformationByHandle(self, handle, info_ref):
            info_ref._obj.dwFileAttributes = 0x80
            return True

        def CloseHandle(self, handle):
            pass

    class FakeLockFile(io.BytesIO):
        def close(self):
            called["closed_file"] = True
            super().close()

    fake_lock_file = FakeLockFile()

    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.open_osfhandle = lambda h, f: 55
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    import ctypes
    monkeypatch.setattr(ctypes, "GetLastError", lambda: 0, raising=False)
    monkeypatch.setattr(ctypes, "windll", type("FakeWindll", (), {"kernel32": FakeKernel32()})(), raising=False)
    monkeypatch.setattr("os.fdopen", lambda fd, mode, closefd=True: fake_lock_file)

    lock = ProjectBridgeLock(tmp_path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.touch()
    with pytest.raises(_LockPathUnsafe, match="bridge lock DACL is unsafe"):
        lock._open_lock_file_windows()

    assert called["is_reparse"] is True
    assert called["set_dacl"] is True
    assert called["val_dacl"] is True
    assert called["closed_file"] is True


def test_pipe_reader_immediate_unbuffered_stderr_reading() -> None:
    import io
    import os
    import threading
    from renforge.bridge.launcher import _BoundedPipeReader

    r_fd, w_fd = os.pipe()
    try:
        r_file = io.open(r_fd, "rb", buffering=0)
        event = threading.Event()
        code = [None]
        reader = _BoundedPipeReader(
            stream=r_file,
            watch_startup=True,
            startup_event=event,
            startup_code=code,
        )
        os.write(w_fd, b"RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT\n")
        assert event.wait(timeout=2.0) is True
        assert code[0] == "BRIDGE_INFO_CONFLICT"
    finally:
        try:
            os.close(w_fd)
        except OSError:
            pass
        try:
            r_file.close()
        except OSError:
            pass


def test_pipe_reader_ignores_stdout_spoof() -> None:
    import io
    import os
    import threading
    from renforge.bridge.launcher import _BoundedPipeReader

    r_fd, w_fd = os.pipe()
    try:
        r_file = io.open(r_fd, "rb", buffering=0)
        event = threading.Event()
        code = [None]
        reader = _BoundedPipeReader(
            stream=r_file,
            watch_startup=False,
            startup_event=event,
            startup_code=code,
        )
        os.write(w_fd, b"RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT\n")
        time.sleep(0.1)
        assert event.is_set() is False
    finally:
        os.close(w_fd)
        r_file.close()


def test_pipe_reader_rejects_trailing_whitespace_and_junk() -> None:
    import io
    import os
    import threading
    from renforge.bridge.launcher import _BoundedPipeReader

    r_fd, w_fd = os.pipe()
    try:
        r_file = io.open(r_fd, "rb", buffering=0)
        event = threading.Event()
        code = [None]
        reader = _BoundedPipeReader(
            stream=r_file,
            watch_startup=True,
            startup_event=event,
            startup_code=code,
        )
        os.write(w_fd, b"RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT \n")
        os.write(w_fd, b"RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT extra\n")
        time.sleep(0.1)
        assert event.is_set() is False
    finally:
        os.close(w_fd)
        r_file.close()


def test_pipe_reader_handles_split_chunk_stream() -> None:
    import io
    import os
    import threading
    from renforge.bridge.launcher import _BoundedPipeReader

    r_fd, w_fd = os.pipe()
    try:
        r_file = io.open(r_fd, "rb", buffering=0)
        event = threading.Event()
        code = [None]
        reader = _BoundedPipeReader(
            stream=r_file,
            watch_startup=True,
            startup_event=event,
            startup_code=code,
        )
        os.write(w_fd, b"RENFORGE_BRIDGE_STARTUP_ERR")
        time.sleep(0.05)
        assert event.is_set() is False

        os.write(w_fd, b"OR=BRIDGE_MANIFEST_IDENTITY_MISMATCH\n")
        assert event.wait(timeout=2.0) is True
        assert code[0] == "BRIDGE_MANIFEST_IDENTITY_MISMATCH"
    finally:
        os.close(w_fd)
        r_file.close()
