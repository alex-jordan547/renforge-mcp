from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping, Sequence


DEFAULT_TIMEOUT_SECONDS: Final = 60


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timeout: int | float
    cwd: str | None
    env: Mapping[str, str] | None
    timed_out: bool

    def to_json(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timeout": self.timeout,
            "cwd": self.cwd,
            "env": dict(self.env) if self.env is not None else None,
            "timed_out": self.timed_out,
        }


def _normalize_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    normalized = dict(os.environ)
    normalized.update(env)
    return normalized


def run_command(
    command: str | Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
    check: bool = False,
) -> CommandResult:
    if isinstance(command, str):
        normalized_command = tuple(shlex.split(command))
    else:
        normalized_command = tuple(command)

    if not normalized_command:
        raise ValueError("run_command received an empty command")
    normalized_env = _normalize_env(env)
    normalized_cwd = str(Path(cwd).resolve()) if cwd is not None else None

    try:
        completed = subprocess.run(
            normalized_command,
            cwd=normalized_cwd,
            env=normalized_env,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        used_env = normalized_env
        return CommandResult(
            command=normalized_command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            timeout=timeout,
            cwd=normalized_cwd,
            env=used_env,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=normalized_command,
            returncode=-1,
            stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=f"Command timed out after {timeout}s",
            timeout=timeout,
            cwd=normalized_cwd,
            env=normalized_env,
            timed_out=True,
        )
