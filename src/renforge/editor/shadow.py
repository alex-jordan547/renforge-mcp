from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
import errno
import os
import re
import shutil
import stat
from typing import Any, Iterable

from ..project import RenpyProject
from ..sdk import RenpySdk
from ..util.files import copy_regular_file_nofollow, fsync_directory, write_exclusive_bytes
from ..util.subprocess import run_command
from .constants import MAX_DIAGNOSTICS_BYTES
from .exceptions import EditorError


MAX_SHADOW_FILES = 10_000
MAX_SHADOW_BYTES = 500 * 1024 * 1024
SHADOW_MAX_FILES_ENV = "RENFORGE_SHADOW_MAX_FILES"
SHADOW_MAX_BYTES_ENV = "RENFORGE_SHADOW_MAX_BYTES"

_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".renforge",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "cache",
    "node_modules",
    "saves",
    "venv",
}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".rpyb", ".rpyc", ".rpymc"}

# A linked directory is never followed. Names in this set are trusted to hold
# assets only; every other linked directory could contain .rpy/.rpym source.
_LINK_ASSET_DIR_NAMES = {
    "audio",
    "bgm",
    "cg",
    "image",
    "images",
    "movie",
    "movies",
    "music",
    "se",
    "sfx",
    "sound",
    "sounds",
    "video",
    "videos",
    "voice",
    "voices",
}

_POSITIVE_INT_RE = re.compile(r"^[0-9]+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_REPARSE_POINT_ATTRIBUTE = 0x400


def _is_directory_link(directory_st: os.stat_result) -> bool:
    """Treat symlinks and Windows junctions/reparse points as linked."""
    return stat.S_ISLNK(directory_st.st_mode) or bool(
        getattr(directory_st, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


@dataclass(frozen=True)
class ShadowLintResult:
    ok: bool
    returncode: int
    timed_out: bool
    stdout: str
    stderr: str
    truncated: bool
    complete: bool = True
    degraded: bool = False
    files: int = 0
    bytes: int = 0
    limits: dict[str, int] = field(default_factory=dict)
    largest: dict[str, Any] | None = None
    skipped_links: tuple[dict[str, str], ...] = ()
    shadow_path: str | None = None


@dataclass(frozen=True)
class ShadowLimits:
    max_files: int
    max_bytes: int


@dataclass(frozen=True)
class _ShadowEntry:
    relative_path: str
    source_path: Path | None
    size: int


@dataclass(frozen=True)
class ShadowPlan:
    root: Path
    directories: tuple[str, ...]
    entries: tuple[_ShadowEntry, ...]
    staged: dict[str, bytes]
    limits: ShadowLimits
    files: int
    bytes: int
    largest: dict[str, Any] | None
    skipped_links: tuple[dict[str, str], ...]
    complete: bool
    degraded: bool


def _bounded_output(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    bounded = encoded[:limit].decode("utf-8", errors="ignore")
    return bounded, True


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    value = raw.strip()
    if not _POSITIVE_INT_RE.fullmatch(value) or int(value) <= 0:
        raise EditorError(
            "SHADOW_QUOTA_INVALID",
            f"{name} must be a positive integer",
            {"variable": name, "value": raw},
        )
    return int(value)


def resolve_shadow_limits() -> ShadowLimits:
    """Return quota limits, failing closed on invalid environment overrides."""
    return ShadowLimits(
        max_files=_env_positive_int(SHADOW_MAX_FILES_ENV, MAX_SHADOW_FILES),
        max_bytes=_env_positive_int(SHADOW_MAX_BYTES_ENV, MAX_SHADOW_BYTES),
    )


def _is_excluded_name(
    name: str,
    excluded_names: frozenset[str],
    excluded_prefixes: tuple[str, ...],
) -> bool:
    return name in excluded_names or any(name.startswith(prefix) for prefix in excluded_prefixes)


def _staged_path_candidate(raw_path: str) -> PurePosixPath:
    invalid = EditorError(
        "SHADOW_PATH_INVALID",
        "staged shadow path is invalid",
        {"path": raw_path if isinstance(raw_path, str) else repr(raw_path)},
    )
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise invalid
    raw = raw_path.replace("\\", "/")
    if (
        "\x00" in raw
        or raw.endswith("/")
        or raw.startswith("/")
        or _WINDOWS_DRIVE_RE.match(raw)
    ):
        raise invalid
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise invalid
    return pure


def _resolve_staged_path(raw_path: str, base_paths: set[str]) -> str:
    """Resolve a project-relative or game-relative overlay path.

    ``game/foo`` is ambiguous: it may already be project-relative or may be a
    game-relative path with a first directory named ``game``. Prefer the
    project-relative reading when that path exists; fall back to the
    game-relative reading, then to the project-relative convention for new
    files.
    """
    pure = _staged_path_candidate(raw_path)
    project_relative = pure.as_posix()
    game_relative = "game/" + project_relative
    if pure.parts[0] == "game":
        if project_relative in base_paths:
            return project_relative
        if game_relative in base_paths:
            return game_relative
        return project_relative
    return game_relative


def _normalize_staged_replacements(
    staged_replacements: dict[str, bytes],
    base_paths: set[str],
) -> dict[str, bytes]:
    staged: dict[str, bytes] = {}
    for raw_path, payload in staged_replacements.items():
        relative_path = _resolve_staged_path(raw_path, base_paths)
        if relative_path in staged:
            raise EditorError(
                "SHADOW_PATH_INVALID",
                "staged shadow paths collide after normalization",
                {"path": raw_path, "normalized_path": relative_path},
            )
        staged[relative_path] = payload
    return staged


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _largest_contributor(entries: list[_ShadowEntry]) -> dict[str, Any] | None:
    totals: dict[str, int] = {}
    for entry in entries:
        parts = entry.relative_path.split("/")
        if parts[0] == "game" and len(parts) > 2:
            # Keep the informative first content directory (game/images, ...)
            # instead of collapsing every asset under "game".
            top_level = "/".join(parts[:2])
        elif len(parts) > 1:
            top_level = parts[0]
        else:
            top_level = "."
        totals[top_level] = totals.get(top_level, 0) + entry.size
    if not totals:
        return None
    path = sorted(totals, key=lambda item: (-totals[item], item))[0]
    return {"path": path, "size": totals[path]}


def _first_quota_offender(
    entries: list[_ShadowEntry],
    limits: ShadowLimits,
) -> _ShadowEntry | None:
    file_count = 0
    byte_count = 0
    for entry in entries:
        file_count += 1
        byte_count += entry.size
        if file_count > limits.max_files or byte_count > limits.max_bytes:
            return entry
    return None


def _raise_quota_error(
    *,
    files: int,
    byte_count: int,
    limits: ShadowLimits,
    offending_path: str,
    offending_size: int,
    largest: dict[str, Any] | None,
) -> None:
    raise EditorError(
        "SHADOW_QUOTA_EXCEEDED",
        "shadow project exceeds its file or byte quota",
        {
            "files": int(files),
            "bytes": int(byte_count),
            "max_files": int(limits.max_files),
            "max_bytes": int(limits.max_bytes),
            "offending_path": offending_path,
            "offending_size": int(offending_size),
            "largest_path": largest["path"] if largest is not None else None,
            "largest_size": int(largest["size"]) if largest is not None else 0,
        },
    )


def plan_shadow_project(
    project: RenpyProject,
    *,
    staged_replacements: dict[str, bytes] | None = None,
    excluded_names: Iterable[str] = (),
    excluded_prefixes: Iterable[str] = (),
    limits: ShadowLimits | None = None,
) -> ShadowPlan:
    """Measure and plan a shadow copy without copying any bytes."""
    resolved_limits = limits or resolve_shadow_limits()
    root = project.root.resolve(strict=True)
    names = frozenset(excluded_names)
    prefixes = tuple(excluded_prefixes)
    directories: list[str] = []
    base_entries: list[_ShadowEntry] = []
    skipped_links: list[dict[str, str]] = []
    complete = True

    try:
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for dirname in dirs:
                if dirname in _EXCLUDED_DIRS or _is_excluded_name(dirname, names, prefixes):
                    continue
                directory = current_path / dirname
                directory_st = directory.lstat()
                if _is_directory_link(directory_st):
                    # Linked directories are never followed. Record the skip
                    # instead of aborting; unknown names may hide script source.
                    skipped_links.append(
                        {
                            "path": _relative_posix(root, directory),
                            "reason": "linked_directory_not_followed",
                        }
                    )
                    if dirname.casefold() not in _LINK_ASSET_DIR_NAMES:
                        complete = False
                    continue
                if not stat.S_ISDIR(directory_st.st_mode):
                    raise EditorError(
                        "SHADOW_SPECIAL_FILE",
                        "shadow project contains a non-directory entry",
                        {"path": str(directory)},
                    )
                kept_dirs.append(dirname)
                relative_dir = _relative_posix(root, directory)
                if relative_dir != ".":
                    directories.append(relative_dir)
            dirs[:] = sorted(kept_dirs)

            for filename in sorted(files):
                if _is_excluded_name(filename, names, prefixes):
                    continue
                source_path = current_path / filename
                if source_path.suffix in _EXCLUDED_SUFFIXES:
                    continue
                source_st = source_path.lstat()
                if stat.S_ISLNK(source_st.st_mode) or not stat.S_ISREG(source_st.st_mode):
                    # Linked regular files stay fatal: they could smuggle
                    # arbitrary host content into a validation run.
                    raise EditorError(
                        "SHADOW_SPECIAL_FILE",
                        "shadow project contains a non-regular file",
                        {"path": str(source_path)},
                    )
                base_entries.append(
                    _ShadowEntry(
                        relative_path=_relative_posix(root, source_path),
                        source_path=source_path,
                        size=source_st.st_size,
                    )
                )
    except OSError as exc:
        raise EditorError(
            "SHADOW_COPY_FAILED",
            "could not inspect the project tree",
            {"path": str(root)},
        ) from exc

    entries = list(base_entries)
    base_paths = set(directories) | {entry.relative_path for entry in entries}
    staged = _normalize_staged_replacements(staged_replacements or {}, base_paths)
    index_by_relative = {entry.relative_path: index for index, entry in enumerate(entries)}
    directory_set = set(directories)
    file_set = set(index_by_relative)
    staged_paths = set(staged)
    for relative_path, payload in staged.items():
        if relative_path in directory_set:
            raise EditorError(
                "SHADOW_PATH_INVALID",
                "staged shadow path names a directory",
                {"path": relative_path},
            )
        parent = PurePosixPath(relative_path).parent
        while parent.parts:
            if parent.as_posix() in file_set or parent.as_posix() in staged_paths:
                raise EditorError(
                    "SHADOW_PATH_INVALID",
                    "staged shadow path is nested under a file",
                    {"path": relative_path},
                )
            parent = parent.parent
        entry = _ShadowEntry(relative_path, None, len(payload))
        if relative_path in index_by_relative:
            entries[index_by_relative[relative_path]] = entry
        else:
            index_by_relative[relative_path] = len(entries)
            entries.append(entry)

    file_count = len(entries)
    byte_count = sum(entry.size for entry in entries)
    largest = _largest_contributor(entries)
    offending = _first_quota_offender(entries, resolved_limits)
    if offending is not None:
        _raise_quota_error(
            files=file_count,
            byte_count=byte_count,
            limits=resolved_limits,
            offending_path=offending.relative_path,
            offending_size=offending.size,
            largest=largest,
        )

    return ShadowPlan(
        root=root,
        directories=tuple(directories),
        entries=tuple(entries),
        staged=staged,
        limits=resolved_limits,
        files=file_count,
        bytes=byte_count,
        largest=largest,
        skipped_links=tuple(skipped_links),
        complete=complete,
        degraded=bool(skipped_links),
    )


def _build_shadow_from_plan(plan: ShadowPlan, *, shadow_root: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    try:
        shadow_root.mkdir(parents=True, exist_ok=False)
        for relative_dir in plan.directories:
            destination_dir = shadow_root / relative_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            fsync_directory(destination_dir)

        for entry in plan.entries:
            if entry.relative_path in plan.staged:
                continue
            destination_path = shadow_root / entry.relative_path
            try:
                copied = copy_regular_file_nofollow(
                    entry.source_path,
                    destination_path,
                    max_bytes=plan.limits.max_bytes - byte_count,
                )
            except OSError as exc:
                if exc.errno == errno.EFBIG:
                    _raise_quota_error(
                        files=file_count + 1,
                        byte_count=byte_count + entry.size,
                        limits=plan.limits,
                        offending_path=entry.relative_path,
                        offending_size=entry.size,
                        largest=plan.largest,
                    )
                raise EditorError(
                    "SHADOW_COPY_FAILED",
                    "could not copy a regular file into the shadow project",
                    {"path": str(entry.source_path)},
                ) from exc
            file_count += 1
            byte_count += copied

        for relative_path, payload in plan.staged.items():
            destination_path = shadow_root / relative_path
            previous_size = 0
            try:
                destination_st = destination_path.lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(destination_st.st_mode) or not stat.S_ISREG(destination_st.st_mode):
                    raise EditorError(
                        "SHADOW_SPECIAL_FILE",
                        "staged shadow destination is not a regular file",
                        {"path": str(destination_path)},
                    )
                previous_size = destination_st.st_size
                destination_path.unlink()
            next_files = file_count + 1
            next_bytes = byte_count - previous_size + len(payload)
            if next_files > plan.limits.max_files or next_bytes > plan.limits.max_bytes:
                _raise_quota_error(
                    files=next_files,
                    byte_count=next_bytes,
                    limits=plan.limits,
                    offending_path=relative_path,
                    offending_size=len(payload),
                    largest=plan.largest,
                )
            try:
                write_exclusive_bytes(destination_path, payload, mode=0o644)
            except OSError as exc:
                raise EditorError(
                    "SHADOW_COPY_FAILED",
                    "could not write a staged file into the shadow project",
                    {"path": str(destination_path)},
                ) from exc
            file_count = next_files
            byte_count = next_bytes
        return file_count, byte_count
    except EditorError:
        shutil.rmtree(shadow_root, ignore_errors=True)
        raise
    except (OSError, ValueError) as exc:
        shutil.rmtree(shadow_root, ignore_errors=True)
        raise EditorError(
            "SHADOW_COPY_FAILED",
            "could not build the shadow project",
            {"path": str(shadow_root)},
        ) from exc
    except Exception:
        shutil.rmtree(shadow_root, ignore_errors=True)
        raise


def build_shadow_project(
    project: RenpyProject,
    *,
    shadow_root: Path,
    staged_replacements: dict[str, bytes],
) -> Path:
    plan = plan_shadow_project(project, staged_replacements=staged_replacements)
    _build_shadow_from_plan(plan, shadow_root=shadow_root)
    return shadow_root


def remove_shadow_project(shadow_root: Path) -> None:
    def _clear_readonly(func: Any, path: str, _exc_info: Any) -> None:
        # Windows marks some copied modes read-only; clear the bit so rmtree
        # does not raise PermissionError after a successful lint.
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
        except OSError:
            raise

    try:
        shutil.rmtree(shadow_root, onerror=_clear_readonly)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise EditorError(
            "SHADOW_CLEANUP_FAILED",
            "could not remove the validation shadow project",
        ) from exc


def run_shadow_lint(
    project: RenpyProject,
    sdk: RenpySdk,
    *,
    shadow_root: Path,
    timeout: float,
) -> ShadowLintResult:
    command = sdk.launch_command(shadow_root.resolve(strict=True), "lint")
    result = run_command(command, timeout=timeout)
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""
    bounded_stdout, trunc_stdout = _bounded_output(stdout_text, MAX_DIAGNOSTICS_BYTES)
    bounded_stderr, trunc_stderr = _bounded_output(stderr_text, MAX_DIAGNOSTICS_BYTES)
    truncated = trunc_stdout or trunc_stderr
    return ShadowLintResult(
        # A truncated transcript cannot prove a clean lint, even with rc 0.
        ok=result.returncode == 0 and not result.timed_out and not truncated,
        returncode=result.returncode,
        timed_out=result.timed_out,
        stdout=bounded_stdout,
        stderr=bounded_stderr,
        truncated=truncated,
    )


def validate_shadow_project(
    project: RenpyProject,
    sdk: RenpySdk,
    *,
    shadow_root: Path,
    staged_replacements: dict[str, bytes] | None = None,
    timeout: float,
    excluded_names: Iterable[str] = (),
    excluded_prefixes: Iterable[str] = (),
    require_source_complete: bool = False,
    keep_shadow: bool = False,
) -> ShadowLintResult:
    """Build, lint, and clean up a shadow copy in one reusable step."""
    limits = resolve_shadow_limits()
    plan = plan_shadow_project(
        project,
        staged_replacements=staged_replacements,
        excluded_names=excluded_names,
        excluded_prefixes=excluded_prefixes,
        limits=limits,
    )
    if require_source_complete and not plan.complete:
        raise EditorError(
            "SHADOW_SOURCE_INCOMPLETE",
            "shadow project omits linked source directories",
            {"skipped_links": [dict(item) for item in plan.skipped_links]},
        )

    file_count, byte_count = _build_shadow_from_plan(plan, shadow_root=shadow_root)
    try:
        lint_result = run_shadow_lint(project, sdk, shadow_root=shadow_root, timeout=timeout)
    except EditorError:
        if not keep_shadow:
            remove_shadow_project(shadow_root)
        raise
    except (OSError, ValueError) as exc:
        if not keep_shadow:
            remove_shadow_project(shadow_root)
        raise EditorError(
            "SHADOW_LINT_FAILED",
            "could not run Ren'Py lint in the shadow project",
        ) from exc
    except Exception:
        if not keep_shadow:
            remove_shadow_project(shadow_root)
        raise

    result = replace(
        lint_result,
        ok=lint_result.ok and plan.complete,
        complete=plan.complete,
        degraded=plan.degraded,
        files=file_count,
        bytes=byte_count,
        limits={"max_files": limits.max_files, "max_bytes": limits.max_bytes},
        largest=plan.largest,
        skipped_links=plan.skipped_links,
        shadow_path=str(shadow_root) if keep_shadow else None,
    )
    if not keep_shadow:
        remove_shadow_project(shadow_root)
    return result
