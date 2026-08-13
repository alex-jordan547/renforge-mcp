from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import ensure_nofollow_directory, write_atomic

CAPTURE_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,80}$"
CAPTURE_NAME_FORBIDDEN = (".", "..")
_CAPTURE_BASENAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")


def capture_name_json_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": CAPTURE_NAME_PATTERN,
        "not": {"enum": list(CAPTURE_NAME_FORBIDDEN)},
    }


def validate_capture_name(value: Any) -> str:
    if not isinstance(value, str) or not _CAPTURE_BASENAME_RE.fullmatch(value):
        raise ValueError("capture name must contain only letters, digits, dot, dash, or underscore")
    if value in CAPTURE_NAME_FORBIDDEN:
        raise ValueError("capture name must be a basename, not a path")
    return value


def write_project_capture(project_path: str | Path, name: Any, data: bytes) -> tuple[Path, Path]:
    project_root = Path(project_path).expanduser().resolve()
    capture_dir = ensure_nofollow_directory(project_root / ".renforge" / "captures")
    target = capture_dir / (validate_capture_name(name) + ".png")
    write_atomic(target, data, follow_symlinks=False)
    return project_root, target


__all__ = ["CAPTURE_NAME_FORBIDDEN", "CAPTURE_NAME_PATTERN", "capture_name_json_schema", "validate_capture_name", "write_project_capture"]
