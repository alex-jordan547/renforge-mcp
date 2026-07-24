import os
from pathlib import Path

from renforge.build import _launcher_command
from renforge.sdk import RenpySdk


_LAUNCHER_NAME = "renpy.exe" if os.name == "nt" else "renpy.sh"


def _fake_sdk(tmp_path: Path) -> RenpySdk:
    root = tmp_path / "sdk"
    (root / "launcher").mkdir(parents=True)
    (root / _LAUNCHER_NAME).write_text("#!/bin/sh\n")
    return RenpySdk(version="8.3.7", root=root)


def test_launcher_command_targets_the_launcher_project(tmp_path: Path) -> None:
    sdk = _fake_sdk(tmp_path)
    cmd = _launcher_command(sdk, "web_build", "/path/to/game", "--destination", "/out")

    assert Path(cmd[0]).name == _LAUNCHER_NAME
    assert Path(cmd[1]).name == "launcher"  # launcher is the base directory
    assert cmd[2] == "web_build"
    assert "/path/to/game" in cmd
    assert cmd[-2:] == ["--destination", "/out"]
