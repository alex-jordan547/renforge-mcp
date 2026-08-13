from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import ensure_nofollow_directory, write_atomic

_CAPTURE_BASENAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")


def validate_capture_name(value: Any) -> str:
    if not isinstance(value, str) or not _CAPTURE_BASENAME_RE.fullmatch(value):
        raise ValueError("capture name must contain only letters, digits, dot, dash, or underscore")
    if value in {".", ".."}:
        raise ValueError("capture name must be a basename, not a path")
    return value


def write_project_capture(project_path: str | Path, name: Any, data: bytes) -> tuple[Path, Path]:
    project_root = Path(project_path).expanduser().resolve()
    capture_dir = ensure_nofollow_directory(project_root / ".renforge" / "captures")
    target = capture_dir / (validate_capture_name(name) + ".png")
    write_atomic(target, data, follow_symlinks=False)
    return project_root, target


__all__ = ["validate_capture_name", "write_project_capture"]
