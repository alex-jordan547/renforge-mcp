from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import errno
import os
import shutil
import stat

from ..project import RenpyProject
from ..sdk import RenpySdk
from ..util.files import copy_regular_file_nofollow, fsync_directory, write_exclusive_bytes
from ..util.subprocess import run_command
from .constants import MAX_DIAGNOSTICS_BYTES
from .exceptions import EditorError


MAX_SHADOW_FILES = 10_000
MAX_SHADOW_BYTES = 500 * 1024 * 1024
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


@dataclass(frozen=True)
class ShadowLintResult:
    ok: bool
    returncode: int
    timed_out: bool
    stdout: str
    stderr: str
    truncated: bool


def _bounded_output(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    bounded = encoded[:limit].decode("utf-8", errors="ignore")
    return bounded, True


def build_shadow_project(
    project: RenpyProject,
    *,
    shadow_root: Path,
    staged_replacements: dict[str, bytes],
) -> Path:
    root = project.root.resolve(strict=True)
    file_count = 0
    byte_count = 0
    try:
        shadow_root.mkdir(parents=True, exist_ok=False)
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            rel_dir = current_path.relative_to(root)
            kept_dirs: list[str] = []
            for dirname in dirs:
                if dirname in _EXCLUDED_DIRS:
                    continue
                directory = current_path / dirname
                directory_st = directory.lstat()
                if stat.S_ISLNK(directory_st.st_mode) or not stat.S_ISDIR(directory_st.st_mode):
                    raise EditorError(
                        "SHADOW_SPECIAL_FILE",
                        "shadow project contains a non-directory entry",
                        {"path": str(directory)},
                    )
                kept_dirs.append(dirname)
            dirs[:] = kept_dirs
            destination_dir = shadow_root / rel_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            fsync_directory(destination_dir)

            for filename in files:
                source_path = current_path / filename
                if source_path.suffix in _EXCLUDED_SUFFIXES:
                    continue
                source_st = source_path.lstat()
                if stat.S_ISLNK(source_st.st_mode) or not stat.S_ISREG(source_st.st_mode):
                    raise EditorError(
                        "SHADOW_SPECIAL_FILE",
                        "shadow project contains a non-regular file",
                        {"path": str(source_path)},
                    )
                if file_count + 1 > MAX_SHADOW_FILES or byte_count + source_st.st_size > MAX_SHADOW_BYTES:
                    raise EditorError(
                        "SHADOW_QUOTA_EXCEEDED",
                        "shadow project exceeds its file or byte quota",
                    )
                destination_path = destination_dir / filename
                try:
                    copied = copy_regular_file_nofollow(
                        source_path,
                        destination_path,
                        max_bytes=MAX_SHADOW_BYTES - byte_count,
                    )
                except OSError as exc:
                    if exc.errno == errno.EFBIG:
                        raise EditorError(
                            "SHADOW_QUOTA_EXCEEDED",
                            "shadow project exceeds its file or byte quota",
                        ) from exc
                    raise EditorError(
                        "SHADOW_COPY_FAILED",
                        "could not copy a regular file into the shadow project",
                        {"path": str(source_path)},
                    ) from exc
                file_count += 1
                byte_count += copied

        game_shadow = shadow_root / "game"
        for relative_path, payload in staged_replacements.items():
            relative = Path(relative_path)
            destination = game_shadow / relative
            if relative.is_absolute() or ".." in relative.parts or not destination.is_relative_to(game_shadow):
                raise EditorError("SHADOW_PATH_INVALID", "staged shadow path is invalid")
            previous_size = 0
            try:
                destination_st = destination.lstat()
                existed = True
            except FileNotFoundError:
                existed = False
            if existed:
                if stat.S_ISLNK(destination_st.st_mode) or not stat.S_ISREG(destination_st.st_mode):
                    raise EditorError(
                        "SHADOW_SPECIAL_FILE",
                        "staged shadow destination is not a regular file",
                        {"path": str(destination)},
                    )
                previous_size = destination_st.st_size
            next_files = file_count if existed else file_count + 1
            next_bytes = byte_count - previous_size + len(payload)
            if next_files > MAX_SHADOW_FILES or next_bytes > MAX_SHADOW_BYTES:
                raise EditorError(
                    "SHADOW_QUOTA_EXCEEDED",
                    "shadow project exceeds its file or byte quota",
                )
            if existed:
                destination.unlink()
            try:
                write_exclusive_bytes(destination, payload, mode=0o644)
            except OSError as exc:
                raise EditorError(
                    "SHADOW_COPY_FAILED",
                    "could not write a staged file into the shadow project",
                    {"path": str(destination)},
                ) from exc
            file_count = next_files
            byte_count = next_bytes
        return shadow_root
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
    return ShadowLintResult(
        ok=result.returncode == 0 and not result.timed_out,
        returncode=result.returncode,
        timed_out=result.timed_out,
        stdout=bounded_stdout,
        stderr=bounded_stderr,
        truncated=trunc_stdout or trunc_stderr,
    )
