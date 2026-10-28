from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
import errno
import os
import re
import shutil
import stat
import time
from typing import Any, Iterable

from ..project import RenpyProject
from ..sdk import RenpySdk
from ..util.files import copy_regular_file_nofollow, fsync_directory, write_exclusive_bytes
from ..util.subprocess import run_command
from .constants import MAX_DIAGNOSTICS_BYTES
from .exceptions import EditorError


MAX_SHADOW_FILES = 10_000
MAX_SHADOW_BYTES = 500 * 1024 * 1024
MAX_LINKED_SOURCE_SCAN_ENTRIES = 50_000
MAX_LINKED_SOURCE_SCAN_SECONDS = 5.0
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
_SOURCE_SUFFIXES = {".rpy", ".rpym"}
# Lint resolves media names but never reads their bytes, so media files become
# zero-byte placeholders instead of consuming the shadow's byte quota.
_MEDIA_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".avif",
    ".gif",
    ".bmp",
    ".tga",
    ".ogg",
    ".opus",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".webm",
    ".mp4",
    ".mkv",
    ".ogv",
    ".avi",
    ".mov",
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
class _LinkedDirectoryScan:
    """Read-only inventory of a linked directory's target.

    ``source_found`` is True when Ren'Py source exists, False when the whole
    target was scanned and holds none, and None when it cannot be fully
    inspected (broken link, nested link/special entry, unreadable subtree,
    entry cap, or time budget). ``directories`` and ``files`` are relative
    POSIX paths inside the target; they are only meaningful for a source-free,
    fully inspected target and never include file contents.
    """

    source_found: bool | None
    directories: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


_UNINSPECTABLE_LINKED_DIRECTORY = _LinkedDirectoryScan(source_found=None)


def _scan_linked_directory(link: Path) -> _LinkedDirectoryScan:
    """Inspect a linked directory's target without reading or copying bytes."""
    try:
        target = link.resolve(strict=True)
    except (OSError, RuntimeError):
        return _UNINSPECTABLE_LINKED_DIRECTORY
    if not target.is_dir():
        return _UNINSPECTABLE_LINKED_DIRECTORY

    deadline = time.monotonic() + MAX_LINKED_SOURCE_SCAN_SECONDS
    inspected = 0
    directories: list[str] = []
    files: list[str] = []
    complete = True

    def _record_error(_exc: OSError) -> None:
        nonlocal complete
        complete = False

    def _budget_exceeded() -> bool:
        return (
            inspected > MAX_LINKED_SOURCE_SCAN_ENTRIES
            or time.monotonic() > deadline
        )

    for current, dirs, filenames in os.walk(
        target, topdown=True, followlinks=False, onerror=_record_error
    ):
        current_path = Path(current)
        relative_current = current_path.relative_to(target)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            inspected += 1
            if _budget_exceeded():
                return _UNINSPECTABLE_LINKED_DIRECTORY
            if dirname in _EXCLUDED_DIRS:
                continue
            child = current_path / dirname
            try:
                child_st = child.lstat()
            except OSError:
                complete = False
                continue
            if _is_directory_link(child_st) or not stat.S_ISDIR(child_st.st_mode):
                # A nested link or special entry leaves part of the target
                # outside the inspected tree.
                complete = False
                continue
            kept_dirs.append(dirname)
            directories.append((relative_current / dirname).as_posix())
        dirs[:] = kept_dirs

        for filename in sorted(filenames):
            inspected += 1
            if _budget_exceeded():
                return _UNINSPECTABLE_LINKED_DIRECTORY
            file_path = current_path / filename
            try:
                file_st = file_path.lstat()
            except OSError:
                complete = False
                continue
            if stat.S_ISLNK(file_st.st_mode) or not stat.S_ISREG(file_st.st_mode):
                # Linked or special files cannot be mirrored as placeholders
                # and may hide source, so the target is not fully inspectable.
                complete = False
                continue
            suffix = Path(filename).suffix.casefold()
            if suffix in _SOURCE_SUFFIXES:
                return _LinkedDirectoryScan(source_found=True)
            if suffix in _EXCLUDED_SUFFIXES:
                continue
            files.append((relative_current / filename).as_posix())

    if not complete:
        return _UNINSPECTABLE_LINKED_DIRECTORY
    return _LinkedDirectoryScan(
        source_found=False,
        directories=tuple(directories),
        files=tuple(files),
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
    placeholders: int = 0
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
    placeholder: bool = False


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


def _resolve_staged_path(raw_path: str, base_paths: dict[str, str]) -> str:
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
        if _path_key(project_relative) in base_paths:
            return base_paths[_path_key(project_relative)]
        if _path_key(game_relative) in base_paths:
            return base_paths[_path_key(game_relative)]
        return project_relative
    return base_paths.get(_path_key(game_relative), game_relative)


def _normalize_staged_replacements(
    staged_replacements: dict[str, bytes],
    base_paths: dict[str, str],
) -> dict[str, bytes]:
    staged: dict[str, bytes] = {}
    seen: set[str] = set()
    for raw_path, payload in staged_replacements.items():
        relative_path = _resolve_staged_path(raw_path, base_paths)
        key = _path_key(relative_path)
        if key in seen:
            raise EditorError(
                "SHADOW_PATH_INVALID",
                "staged shadow paths collide after normalization",
                {"path": raw_path, "normalized_path": relative_path},
            )
        seen.add(key)
        staged[relative_path] = payload
    return staged


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_key(path: str) -> str:
    """Compare staged paths the way the local filesystem compares names."""
    return os.path.normcase(path)


def _is_game_relative(relative_dir: str) -> bool:
    """True when the path's top-level component is ``game`` (case-insensitive).

    Windows junctions may spell the game directory with any case; Ren'Py loads
    it either way, so the linked-directory policy must match that behavior.
    """
    parts = PurePosixPath(relative_dir).parts
    return bool(parts) and parts[0].casefold() == "game"


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
    game_root = root / "game"
    directories: list[str] = []
    base_entries: list[_ShadowEntry] = []
    base_bytes = 0
    skipped_links: list[dict[str, str]] = []
    complete = True

    def _check_base_quota() -> None:
        if len(base_entries) <= resolved_limits.max_files and base_bytes <= resolved_limits.max_bytes:
            return
        offending = base_entries[-1]
        _raise_quota_error(
            files=len(base_entries),
            byte_count=base_bytes,
            limits=resolved_limits,
            offending_path=offending.relative_path,
            offending_size=offending.size,
            largest=_largest_contributor(base_entries),
        )

    def _on_walk_error(exc: OSError) -> None:
        # os.walk otherwise skips unreadable directories silently, which would
        # make an incomplete tree look clean.
        raise EditorError(
            "SHADOW_COPY_FAILED",
            "could not inspect the project tree",
            {"path": str(exc.filename or root)},
        ) from exc

    try:
        for current, dirs, files in os.walk(
            root, topdown=True, followlinks=False, onerror=_on_walk_error
        ):
            current_path = Path(current)
            at_game_root = current_path == game_root
            kept_dirs: list[str] = []
            for dirname in dirs:
                if dirname in _EXCLUDED_DIRS or (
                    at_game_root and _is_excluded_name(dirname, names, prefixes)
                ):
                    continue
                directory = current_path / dirname
                directory_st = directory.lstat()
                if _is_directory_link(directory_st):
                    # Linked directories are never copied byte-for-byte. A
                    # link under game/ can still hide script source, so scan
                    # its target read-only before declaring the shadow complete.
                    relative_dir = _relative_posix(root, directory)
                    reason = "linked_directory_not_followed"
                    if _is_game_relative(relative_dir):
                        scan = _scan_linked_directory(directory)
                        if scan.source_found is True:
                            reason = "linked_directory_with_source"
                            complete = False
                        elif scan.source_found is None:
                            reason = "linked_directory_uninspectable"
                            complete = False
                        else:
                            # A fully inspected, source-free asset directory
                            # is mirrored with zero-byte placeholders so lint
                            # still resolves expected image/audio names.
                            reason = "linked_directory_assets_placeholders"
                            prefix = PurePosixPath(relative_dir)
                            directories.append(relative_dir)
                            directories.extend(
                                (prefix / item).as_posix() for item in scan.directories
                            )
                            base_entries.extend(
                                _ShadowEntry(
                                    relative_path=(prefix / item).as_posix(),
                                    source_path=None,
                                    size=0,
                                    placeholder=True,
                                )
                                for item in scan.files
                            )
                            _check_base_quota()
                    skipped_links.append({"path": relative_dir, "reason": reason})
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
                if at_game_root and _is_excluded_name(filename, names, prefixes):
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
                relative_path = _relative_posix(root, source_path)
                if source_path.suffix.casefold() in _MEDIA_SUFFIXES:
                    base_entries.append(
                        _ShadowEntry(
                            relative_path=relative_path,
                            source_path=None,
                            size=0,
                            placeholder=True,
                        )
                    )
                    _check_base_quota()
                    continue
                base_entries.append(
                    _ShadowEntry(
                        relative_path=relative_path,
                        source_path=source_path,
                        size=source_st.st_size,
                    )
                )
                base_bytes += source_st.st_size
                _check_base_quota()
    except OSError as exc:
        raise EditorError(
            "SHADOW_COPY_FAILED",
            "could not inspect the project tree",
            {"path": str(exc.filename or root)},
        ) from exc

    entries = list(base_entries)
    base_paths = {_path_key(path): path for path in directories}
    base_paths.update({_path_key(entry.relative_path): entry.relative_path for entry in entries})
    staged = _normalize_staged_replacements(staged_replacements or {}, base_paths)
    index_by_relative = {_path_key(entry.relative_path): index for index, entry in enumerate(entries)}
    directory_set = {_path_key(path) for path in directories}
    file_set = set(index_by_relative)
    staged_paths = {_path_key(path) for path in staged}
    for relative_path, payload in staged.items():
        key = _path_key(relative_path)
        if key in directory_set:
            raise EditorError(
                "SHADOW_PATH_INVALID",
                "staged shadow path names a directory",
                {"path": relative_path},
            )
        parent = PurePosixPath(relative_path).parent
        while parent.parts:
            parent_key = _path_key(parent.as_posix())
            if parent_key in file_set or parent_key in staged_paths:
                raise EditorError(
                    "SHADOW_PATH_INVALID",
                    "staged shadow path is nested under a file",
                    {"path": relative_path},
                )
            parent = parent.parent
        entry = _ShadowEntry(relative_path, None, len(payload))
        if key in index_by_relative:
            entries[index_by_relative[key]] = entry
        else:
            index_by_relative[key] = len(entries)
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
        # The copy holds staged source, so keep it private even when the caller
        # puts it somewhere other than a 0700 temp parent.
        shadow_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        for relative_dir in plan.directories:
            destination_dir = shadow_root / relative_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            fsync_directory(destination_dir)

        for entry in plan.entries:
            if entry.relative_path in plan.staged:
                continue
            destination_path = shadow_root / entry.relative_path
            if entry.placeholder:
                # Source-free linked assets are represented by empty files;
                # nothing but the expected names is materialized.
                try:
                    write_exclusive_bytes(destination_path, b"", mode=0o644)
                except OSError as exc:
                    raise EditorError(
                        "SHADOW_COPY_FAILED",
                        "could not write a placeholder file into the shadow project",
                        {"path": destination_path.as_posix()},
                    ) from exc
                file_count += 1
                continue
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
            # Staged entries are skipped during the copy, so the destination
            # never exists yet; the payload size is the only new weight.
            next_files = file_count + 1
            next_bytes = byte_count + len(payload)
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
    error_code: bool = True,
) -> ShadowLintResult:
    command = sdk.launch_command(shadow_root.resolve(strict=True), "lint")
    if error_code:
        command.append("--error-code")
    result = run_command(command, timeout=timeout)
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""
    bounded_stdout, trunc_stdout = _bounded_output(stdout_text, MAX_DIAGNOSTICS_BYTES)
    bounded_stderr, trunc_stderr = _bounded_output(stderr_text, MAX_DIAGNOSTICS_BYTES)
    truncated = trunc_stdout or trunc_stderr
    # With --error-code the return code is the verdict, so a truncated
    # transcript must not turn a failing lint into a passing one. Without it
    # the transcript is the only evidence, and the editor's lenient gate
    # already accepts reported lint errors: a long transcript alone must not
    # become a commit blocker there. `truncated` is reported either way.
    ok = result.returncode == 0 and not result.timed_out and not (
        truncated and error_code
    )
    return ShadowLintResult(
        ok=ok,
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
    error_code: bool = True,
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
        lint_result = run_shadow_lint(
            project,
            sdk,
            shadow_root=shadow_root,
            timeout=timeout,
            error_code=error_code,
        )
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
        placeholders=sum(1 for entry in plan.entries if entry.placeholder),
        limits={"max_files": limits.max_files, "max_bytes": limits.max_bytes},
        largest=plan.largest,
        skipped_links=plan.skipped_links,
        shadow_path=str(shadow_root) if keep_shadow else None,
    )
    if not keep_shadow:
        remove_shadow_project(shadow_root)
    return result
