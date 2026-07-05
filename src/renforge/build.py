"""Build tools: package a project for the web or as desktop distributions.

``web_build`` and ``distribute`` are *launcher* commands, so they run with the
SDK's ``launcher`` project as the base directory and the target project as an
argument: ``renpy.sh <sdk>/launcher <command> <project> ...``.

``web_build`` requires Ren'Py's web support ("web" DLC). If it isn't installed
the command reports that rather than building; installing it is a one-time,
interactive step outside this tool.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence

from .project import RenpyProject
from .sdk import RenpySdk
from .util.subprocess import run_command


def _launcher_command(sdk: RenpySdk, command: str, *args: str) -> list[str]:
    launcher = sdk.root / "launcher"
    if sdk.launcher.suffix == ".py":
        base = [sys.executable, str(sdk.launcher)]
    else:
        base = [str(sdk.launcher)]
    return [*base, str(launcher), command, *args]


def web_build(sdk: RenpySdk, project: RenpyProject, *, destination: str | Path | None = None, timeout: int = 600) -> dict[str, Any]:
    """Package ``project`` as a browser-playable build (needs the web DLC)."""
    args: list[str] = [str(project.abs_root)]
    if destination is not None:
        args += ["--destination", str(Path(destination).expanduser().resolve())]
    result = run_command(_launcher_command(sdk, "web_build", *args), timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "destination": str(destination) if destination else None,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "timed_out": result.timed_out,
    }


def distribute(
    sdk: RenpySdk,
    project: RenpyProject,
    *,
    packages: Sequence[str] | None = None,
    destination: str | Path | None = None,
    build_update: bool = False,
    timeout: int = 900,
) -> dict[str, Any]:
    """Build desktop distributions (Windows/mac/Linux) for ``project``."""
    args: list[str] = []
    if destination is not None:
        args += ["--destination", str(Path(destination).expanduser().resolve())]
    if not build_update:
        args += ["--no-update"]
    for package in packages or ():
        args += ["--package", package]
    args.append(str(project.abs_root))

    result = run_command(_launcher_command(sdk, "distribute", *args), timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "packages": list(packages) if packages else None,
        "destination": str(destination) if destination else None,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "timed_out": result.timed_out,
    }


__all__ = ["web_build", "distribute"]
