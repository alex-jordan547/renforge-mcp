"""Tests for stopping a game through the published bridge (cross-process)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from renforge.tools import live


def _make_project(tmp_path: Path, *, with_bridge: bool = True, pid: int = 4242) -> Path:
    project = tmp_path / "project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    if with_bridge:
        renforge = project / ".renforge"
        renforge.mkdir(parents=True)
        (renforge / "bridge.json").write_text(
            json.dumps({"host": "127.0.0.1", "port": 65123, "token": "t", "pid": pid}),
            encoding="utf-8",
        )
        (project / "game" / "renforge_bridge.rpy").write_text("# injected\n", encoding="utf-8")
        (project / "game" / "renforge_bridge.rpyc").write_bytes(b"\x00")
    return project


class _AliveClient:
    def ping(self) -> dict:
        return {"ok": True, "pong": True}


class _DeadClient:
    def ping(self) -> dict:
        raise ConnectionRefusedError("no bridge")


class _StateClient(_AliveClient):
    def get_state(self) -> dict:
        return {"current_label": "dashboard_scene"}


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
    project = _make_project(tmp_path, with_bridge=False)
    assert live.stop_game(str(project)) == {"ok": True, "was_running": False}


def test_stop_game_terminates_recorded_pid_when_bridge_alive(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path, pid=4242)
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=1.0: _AliveClient()),
    )
    killed: dict[str, int] = {}

    def fake_terminate(pid: int, **_kwargs) -> bool:
        killed["pid"] = pid
        return True

    monkeypatch.setattr(live, "_terminate_pid", fake_terminate)

    result = live.stop_game(str(project))

    assert result == {"ok": True, "was_running": True}
    assert killed["pid"] == 4242
    assert not (project / "game" / "renforge_bridge.rpy").exists()
    assert not (project / "game" / "renforge_bridge.rpyc").exists()
    assert not (project / ".renforge" / "bridge.json").exists()


def test_stop_game_cleans_stale_bridge_without_killing(tmp_path: Path, monkeypatch) -> None:
    project = _make_project(tmp_path, pid=4242)
    monkeypatch.setattr(
        live.BridgeClient,
        "from_project",
        classmethod(lambda cls, root, *, timeout=1.0: _DeadClient()),
    )
    calls = {"terminated": False}

    def fake_terminate(*_args, **_kwargs) -> bool:
        calls["terminated"] = True
        return True

    monkeypatch.setattr(live, "_terminate_pid", fake_terminate)

    result = live.stop_game(str(project))

    assert result == {"ok": True, "was_running": False}
    assert calls["terminated"] is False  # a dead bridge is never killed by PID
    assert not (project / ".renforge" / "bridge.json").exists()
    assert not (project / "game" / "renforge_bridge.rpy").exists()


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

    assert result == {
        "ok": True,
        "already_running": True,
        "external": True,
        "ready": True,
        "current_label": "dashboard_scene",
    }


def test_start_launch_returns_before_slow_startup_and_exposes_ready_status(tmp_path: Path) -> None:
    project = _make_project(tmp_path, with_bridge=False)
    release = threading.Event()
    started = threading.Event()

    def delayed_launch(_cancel_event: threading.Event) -> dict:
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
        lambda _cancel_event: ignored_launch.set() or {"ok": True, "ready": True},
        wait_timeout=0.0,
    )
    assert conflict["ok"] is False
    assert conflict["code"] == "LAUNCH_IN_PROGRESS"
    assert conflict["status"] == "starting"
    assert ignored_launch.is_set() is False

    release.set()
    assert _wait_for_launch_status(project, "ready")["ready"] is True
    live.stop_game(str(project))


def test_launch_status_exposes_a_failed_startup(tmp_path: Path) -> None:
    project = _make_project(tmp_path, with_bridge=False)

    result = live.start_launch(
        str(project),
        lambda _cancel_event: {
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
    project = _make_project(tmp_path, with_bridge=False)
    started = threading.Event()

    def cancellable_launch(cancel_event: threading.Event) -> dict:
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


def test_stop_game_attempts_external_stop_when_cancellation_is_still_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _make_project(tmp_path, with_bridge=False)
    release = threading.Event()
    started = threading.Event()

    def slow_cancel(_cancel_event: threading.Event) -> dict:
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
