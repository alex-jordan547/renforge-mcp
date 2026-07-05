"""Translation tools: list languages, generate translations, report completion.

Wraps Ren'Py's built-in translation CLI commands. Completion stats come from
``translate <language> --count``, which prints the number of missing dialogue
and string translations without writing files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .project import RenpyProject
from .sdk import RenpySdk
from .util.subprocess import run_command

_TL_DIR = "tl"
_RESERVED = {"None", "none"}
_COUNT_RE = re.compile(
    r"(?P<dialogue>\d+)\s+missing\s+dialogue\s+translations.*?(?P<strings>\d+)\s+missing\s+string\s+translations",
    re.IGNORECASE | re.DOTALL,
)


def list_languages(project_path: str | Path) -> list[str]:
    """Return the translation languages present under ``game/tl/``."""
    tl = Path(project_path).expanduser().resolve() / "game" / _TL_DIR
    if not tl.is_dir():
        return []
    return sorted(
        d.name for d in tl.iterdir() if d.is_dir() and d.name not in _RESERVED
    )


def generate_translations(sdk: RenpySdk, project: RenpyProject, language: str, *, timeout: int = 180) -> dict[str, Any]:
    """Generate/update translation files for ``language`` (writes game/tl/<language>/)."""
    command = project.renpy_command(sdk, ("translate", language))
    result = run_command(command, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "language": language,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def translation_stats(sdk: RenpySdk, project: RenpyProject, language: str, *, timeout: int = 180) -> dict[str, Any]:
    """Report missing dialogue/string counts for ``language`` (no files written)."""
    command = project.renpy_command(sdk, ("translate", language, "--count"))
    result = run_command(command, timeout=timeout)
    match = _COUNT_RE.search(result.stdout) or _COUNT_RE.search(result.stderr)
    stats: dict[str, Any] = {
        "ok": result.returncode == 0,
        "language": language,
    }
    if match:
        stats["missing_dialogue"] = int(match.group("dialogue"))
        stats["missing_strings"] = int(match.group("strings"))
    else:
        stats["missing_dialogue"] = None
        stats["missing_strings"] = None
        stats["raw"] = (result.stdout or result.stderr).strip()[:500]
    return stats


def export_dialogue(sdk: RenpySdk, project: RenpyProject, language: str = "None", *, timeout: int = 180) -> dict[str, Any]:
    """Export the game's dialogue as plain text."""
    command = project.renpy_command(sdk, ("dialogue", language, "--text"))
    result = run_command(command, timeout=timeout)
    return {
        "ok": result.returncode == 0,
        "language": language,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


__all__ = ["list_languages", "generate_translations", "translation_stats", "export_dialogue"]
