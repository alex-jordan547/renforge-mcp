from pathlib import Path

import pytest

from renforge.bridge.launcher import BridgeSession, launch_with_bridge, remove_bridge_artifacts
from renforge.project import RenpyProject
from renforge.sdk import RenpySdk


class _FakeProcess:
    def __init__(self):
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


class _FakeClient:
    def ping(self):
        return {"ok": True}


def _make_project(tmp_path: Path) -> tuple[RenpyProject, RenpySdk, Path]:
    project_root = tmp_path / "project"
    (project_root / "game").mkdir(parents=True)
    sdk_root = tmp_path / "sdk"
    sdk_root.mkdir(parents=True)
    (sdk_root / "renpy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return RenpyProject(project_root), RenpySdk(version="8.3.7", root=sdk_root), project_root


@pytest.mark.parametrize("warp", [None, "game/script.rpy:123"])
def test_launch_with_bridge_builds_run_command(monkeypatch, tmp_path: Path, warp: str | None) -> None:
    project, sdk, project_root = _make_project(tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(command, env=None, stdout=None, stderr=None):
        captured["command"] = command
        info_path = project_root / ".renforge" / "bridge.json"
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text("{}", encoding="utf-8")
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("renforge.bridge.launcher.BridgeClient.from_project", lambda _project_root: _FakeClient())

    session = launch_with_bridge(sdk, project, warp=warp)
    assert session is not None
    command = list(captured["command"])  # type: ignore[arg-type]
    assert len(command) >= 3
    assert command[0].endswith("renpy.sh")
    assert command[1] == str(project_root.resolve())
    if warp is None:
        assert command[2:] == ["run"]
    else:
        assert command[2:5] == ["run", "--warp", warp]
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


def test_failed_launch_removes_every_generated_bridge_artifact(monkeypatch, tmp_path: Path) -> None:
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
