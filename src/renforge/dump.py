"""Authoritative project definitions via Ren'Py's native ``--json-dump``.

Running a custom Ren'Py CLI command that executes the game's init code is not
viable headlessly: under a dummy/no-audio driver, common init blocks (e.g.
``00mixers.rpy`` at ``init 1600``) reference ``renpy.music`` / ``renpy.list_files``
and similar attributes that are only wired up during a full display+audio init,
which raises ``AttributeError`` before our command runs.

Ren'Py's built-in ``compile --json-dump`` instead introspects the *parsed*
script without executing init code, so it works in any headless environment. It
yields exact ``file:line`` locations for labels, defines, screens, transforms
and callables. The narrative *flow* graph (jumps / menus / says) comes from the
fast regex scanner (see :mod:`renforge.scanner`); exact AST-level flow is a
runtime concern handled later through the in-game bridge, where every
``renpy.*`` module is fully loaded.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .project import RenpyProject
from .sdk import RenpySdk
from .util.subprocess import run_command

_DEFINITION_CATEGORIES = ("label", "define", "screen", "transform", "callable")


def run_native_dump(sdk: RenpySdk, project: RenpyProject, *, timeout: int = 180) -> dict[str, Any]:
    """Return Ren'Py's native JSON dump for ``project``.

    This compiles the project (writing ``.rpyc`` next to sources, as Ren'Py
    normally does) and introspects the parsed script. Raises ``RuntimeError``
    if Ren'Py produced no dump file.
    """
    out_fd, out_name = tempfile.mkstemp(prefix="renforge-jsondump-", suffix=".json")
    os.close(out_fd)
    out_path = Path(out_name)
    out_path.unlink(missing_ok=True)  # Ren'Py writes <file>.new then renames into place.

    try:
        command = project.renpy_command(sdk, ("compile", "--json-dump", str(out_path)))
        result = run_command(command, timeout=timeout)

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(
                "Ren'Py produced no JSON dump "
                f"(returncode={result.returncode}, timed_out={result.timed_out}).\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        out_path.unlink(missing_ok=True)


def normalize_definitions(raw_dump: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``raw_dump['location']`` into a sorted list of definitions.

    Each entry: ``{"name", "kind", "file", "line"}`` where ``kind`` is one of
    ``label|define|screen|transform|callable``.
    """
    location = raw_dump.get("location", {}) or {}
    definitions: list[dict[str, Any]] = []

    for kind in _DEFINITION_CATEGORIES:
        entries = location.get(kind, {}) or {}
        for name, where in entries.items():
            file_name = where[0] if isinstance(where, (list, tuple)) and where else None
            line = where[1] if isinstance(where, (list, tuple)) and len(where) > 1 else None
            definitions.append({"name": name, "kind": kind, "file": file_name, "line": line})

    definitions.sort(key=lambda d: (d["kind"], d.get("file") or "", d.get("line") or 0))
    return definitions


__all__ = ["run_native_dump", "normalize_definitions"]
