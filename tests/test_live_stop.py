"""Tests for stopping a game through the published bridge (cross-process)."""

from __future__ import annotations

import json
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
