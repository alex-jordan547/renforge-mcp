from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil

from ..project import RenpyProject
from ..sdk import RenpySdk
from ..util.subprocess import run_command
from .constants import MAX_DIAGNOSTICS_BYTES
from .paths import fsync_directory


_EXCLUDED_DIRS = {".renforge", "saves", "cache"}
_EXCLUDED_SUFFIXES = {".rpyc", ".rpymc", ".rpyb"}


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
    shadow_root.mkdir(parents=True, exist_ok=True)

    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        dirs[:] = [name for name in dirs if name not in _EXCLUDED_DIRS]
        destination_dir = shadow_root / rel_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        fsync_directory(destination_dir)

        for filename in files:
            source_path = current_path / filename
            if source_path.suffix in _EXCLUDED_SUFFIXES:
                continue
            if source_path.is_symlink():
                raise ValueError(f"shadow source symlink is not allowed: {source_path}")
            destination_path = destination_dir / filename
            shutil.copy2(source_path, destination_path)

    for relative_path, payload in staged_replacements.items():
        destination = shadow_root / "game" / Path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    return shadow_root


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

