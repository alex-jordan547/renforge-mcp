from pathlib import Path

import pytest

from renforge.bridge.launcher import launch_with_bridge
from renforge.project import RenpyProject
from renforge.sdk import RenpySdk


class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.stdout = None
        self.stderr = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.terminated = True

    def wait(self, timeout: float | None = None):
        self.terminated = True
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
