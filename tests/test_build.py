from pathlib import Path

from renforge.build import _launcher_command
from renforge.sdk import RenpySdk


def _fake_sdk(tmp_path: Path) -> RenpySdk:
    root = tmp_path / "sdk"
    (root / "launcher").mkdir(parents=True)
    (root / "renpy.sh").write_text("#!/bin/sh\n")
    return RenpySdk(version="8.3.7", root=root)


def test_launcher_command_targets_the_launcher_project(tmp_path: Path) -> None:
    sdk = _fake_sdk(tmp_path)
    cmd = _launcher_command(sdk, "web_build", "/path/to/game", "--destination", "/out")

    assert cmd[0].endswith("renpy.sh")
    assert cmd[1].endswith("/launcher")  # launcher is the base directory
    assert cmd[2] == "web_build"
    assert "/path/to/game" in cmd
    assert cmd[-2:] == ["--destination", "/out"]
