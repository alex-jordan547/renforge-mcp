"""Optional write scope for the Live Editor.

``RENFORGE_ALLOW_PATHS`` narrows which files inside ``game/`` a save or undo
may touch. It is a comma-separated list of game-relative POSIX glob patterns:

* ``*``, ``?`` and ``[...]`` match inside one path segment and never cross
  ``/`` (unlike plain ``fnmatch`` on the whole path).
* ``**`` as a whole segment matches zero or more directories.

Matching is case-sensitive POSIX matching on every platform, including
Windows. Drive-letter prefixes and trailing slashes are rejected rather than
silently rewritten; such patterns fail closed like any other invalid pattern.

When the variable is unset or blank, no extra scope applies and the fixed
``game/`` confinement in :mod:`renforge.editor.paths` is the only rule.
Invalid patterns fail closed: every commit and undo is refused with
``PATH_SCOPE_INVALID`` until the environment is fixed.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from .exceptions import EditorError
from .paths import (
    EditorPathError,
    _validate_relative_path,
    resolve_game_path,
    to_game_relative_path,
)

ENV_ALLOW_PATHS = "RENFORGE_ALLOW_PATHS"

_DRIVE_LETTER_PREFIX = re.compile(r"^[A-Za-z]:")


def _validate_scope_pattern(pattern: str) -> PurePosixPath:
    """Reject spellings that only exist on Windows and cannot be matched."""
    if _DRIVE_LETTER_PREFIX.match(pattern):
        raise EditorPathError(
            "PATH_DRIVE_LETTER_REJECTED",
            "drive-letter prefixes are not allowed",
        )
    if pattern.endswith("/"):
        raise EditorPathError(
            "PATH_TRAILING_SLASH_REJECTED",
            "trailing slashes are not allowed",
        )
    return _validate_relative_path(pattern)


def load_path_scope(env: Mapping[str, str] | None = None) -> tuple[str, ...] | None:
    """Return the validated glob patterns, or ``None`` when no scope is set."""
    raw = (os.environ if env is None else env).get(ENV_ALLOW_PATHS)
    patterns = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not patterns:
        return None

    scope: list[str] = []
    for pattern in patterns:
        try:
            normalized = _validate_scope_pattern(pattern)
        except EditorPathError as exc:
            raise EditorError(
                "PATH_SCOPE_INVALID",
                f"invalid {ENV_ALLOW_PATHS} pattern {pattern!r}: {exc}",
                details={"pattern": pattern, "reason": exc.code},
            ) from exc
        scope.append(normalized.as_posix())
    return tuple(scope)


def is_path_in_scope(relative_path: str, scope: Sequence[str]) -> bool:
    """True when a game-relative POSIX path matches at least one pattern."""
    path_parts = tuple(relative_path.split("/"))
    return any(_match_parts(tuple(pattern.split("/")), path_parts) for pattern in scope)


# Match with an explicit stack: a pattern containing many ``**`` segments can
# otherwise hit Python's recursion limit on a deep or non-matching target.
def _match_parts(pattern_parts: tuple[str, ...], path_parts: tuple[str, ...]) -> bool:
    pending = [(0, 0)]
    seen: set[tuple[int, int]] = set()
    while pending:
        pattern_index, path_index = pending.pop()
        state = (pattern_index, path_index)
        if state in seen:
            continue
        seen.add(state)
        if pattern_index == len(pattern_parts):
            if path_index == len(path_parts):
                return True
            continue
        head = pattern_parts[pattern_index]
        if head == "**":
            pending.append((pattern_index + 1, path_index))
            if path_index < len(path_parts):
                pending.append((pattern_index, path_index + 1))
        elif path_index < len(path_parts) and fnmatch.fnmatchcase(
            path_parts[path_index], head
        ):
            pending.append((pattern_index + 1, path_index + 1))
    return False


def require_in_scope(project_root: str | Path, relative_path: str) -> None:
    """Refuse a write target outside ``RENFORGE_ALLOW_PATHS``.

    The target is re-resolved exactly like the editor's write path, so the
    decision is made on the real in-game file rather than on a caller-supplied
    string. Does nothing when the variable is unset.
    """
    scope = load_path_scope()
    if scope is None:
        return

    try:
        resolved = resolve_game_path(project_root, relative_path)
        relative = to_game_relative_path(project_root, resolved)
    except EditorPathError as exc:
        raise EditorError(exc.code, str(exc), details=dict(exc.details)) from exc

    if is_path_in_scope(relative, scope):
        return
    raise EditorError(
        "PATH_OUT_OF_SCOPE",
        f"path {relative} is outside the active editor write scope: {', '.join(scope)}",
        details={"path": relative, "scope": list(scope)},
    )
