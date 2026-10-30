import os
from pathlib import Path
from types import SimpleNamespace

from renforge import build
from renforge.build import _launcher_command
from renforge.project import RenpyProject
from renforge.sdk import RenpySdk


_LAUNCHER_NAME = "renpy.exe" if os.name == "nt" else "renpy.sh"


def _fake_sdk(tmp_path: Path) -> RenpySdk:
    root = tmp_path / "sdk"
    (root / "launcher").mkdir(parents=True)
    (root / _LAUNCHER_NAME).write_text("#!/bin/sh\n")
    return RenpySdk(version="8.3.7", root=root)


def _project(tmp_path: Path) -> RenpyProject:
    root = tmp_path / "proj"
    (root / "game").mkdir(parents=True)
    return RenpyProject(root)


def _fake_run_command(monkeypatch, *, returncode: int = 0) -> None:
    monkeypatch.setattr(
        build,
        "run_command",
        lambda command, timeout: SimpleNamespace(
            returncode=returncode, stdout="", stderr="", timed_out=False
        ),
    )


def test_launcher_command_targets_the_launcher_project(tmp_path: Path) -> None:
    sdk = _fake_sdk(tmp_path)
    cmd = _launcher_command(sdk, "web_build", "/path/to/game", "--destination", "/out")

    assert Path(cmd[0]).name == _LAUNCHER_NAME
    assert Path(cmd[1]).name == "launcher"  # launcher is the base directory
    assert cmd[2] == "web_build"
    assert "/path/to/game" in cmd
    assert cmd[-2:] == ["--destination", "/out"]


def test_web_build_reports_a_project_relative_destination(
    tmp_path: Path, monkeypatch
) -> None:
    _fake_run_command(monkeypatch)
    project = _project(tmp_path)

    result = build.web_build(
        _fake_sdk(tmp_path),
        project,
        destination=project.abs_root / "builds" / "web",
    )

    assert result["ok"] is True
    assert result["files_touched"] == ["builds/web"]


def test_distribute_reports_an_external_destination_as_absolute(
    tmp_path: Path, monkeypatch
) -> None:
    _fake_run_command(monkeypatch)
    destination = tmp_path / "dist"

    result = build.distribute(_fake_sdk(tmp_path), _project(tmp_path), destination=destination)

    assert result["ok"] is True
    assert result["files_touched"] == [destination.resolve().as_posix()]


def test_build_without_a_destination_reports_no_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    _fake_run_command(monkeypatch)
    project = _project(tmp_path)
    sdk = _fake_sdk(tmp_path)

    web = build.web_build(sdk, project)
    desktop = build.distribute(sdk, project)

    # Ren'Py picks a default destination that the tool cannot enumerate, so
    # the receipt is omitted rather than claiming an empty write.
    assert web["ok"] is True
    assert "files_touched" not in web
    assert desktop["ok"] is True
    assert "files_touched" not in desktop


def test_failed_build_reports_no_receipt(tmp_path: Path, monkeypatch) -> None:
    _fake_run_command(monkeypatch, returncode=1)
    project = _project(tmp_path)
    sdk = _fake_sdk(tmp_path)

    web = build.web_build(sdk, project, destination=project.abs_root / "builds" / "web")
    desktop = build.distribute(sdk, project, destination=tmp_path / "dist")

    assert web["ok"] is False
    assert "files_touched" not in web
    assert desktop["ok"] is False
    assert "files_touched" not in desktop
