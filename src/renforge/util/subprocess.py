from __future__ import annotations

import locale
import os
import shlex
import subprocess
import threading
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
    stdout_truncated: bool = False
    stderr_truncated: bool = False

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
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }


def _normalize_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    normalized = dict(os.environ)
    normalized.update(env)
    return normalized


def _decode_bounded_output(raw: bytes) -> str:
    return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _run_command_bounded(
    normalized_command: tuple[str, ...],
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
    timeout: int | float,
    check: bool,
    max_output_bytes: int,
) -> CommandResult:
    process = subprocess.Popen(
        normalized_command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]

    def _drain(stream, buffer: bytearray, index: int) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            remaining = max_output_bytes - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated[index] = True
            else:
                truncated[index] = True

    readers = [
        threading.Thread(target=_drain, args=(process.stdout, buffers[0], 0), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, buffers[1], 1), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        returncode = -1
    for reader in readers:
        reader.join(timeout=5)

    stdout = _decode_bounded_output(bytes(buffers[0]))
    if timed_out:
        stderr = f"Command timed out after {timeout}s"
    else:
        stderr = _decode_bounded_output(bytes(buffers[1]))
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                normalized_command,
                output=stdout,
                stderr=stderr,
            )
    return CommandResult(
        command=normalized_command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timeout=timeout,
        cwd=cwd,
        env=env,
        timed_out=timed_out,
        stdout_truncated=truncated[0],
        stderr_truncated=truncated[1],
    )


def run_command(
    command: str | Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
    check: bool = False,
    max_output_bytes: int | None = None,
) -> CommandResult:
    if isinstance(command, str):
        normalized_command = tuple(shlex.split(command))
    else:
        normalized_command = tuple(command)

    if not normalized_command:
        raise ValueError("run_command received an empty command")
    normalized_env = _normalize_env(env)
    normalized_cwd = str(Path(cwd).resolve()) if cwd is not None else None

    if max_output_bytes is not None:
        return _run_command_bounded(
            normalized_command,
            cwd=normalized_cwd,
            env=normalized_env,
            timeout=timeout,
            check=check,
            max_output_bytes=max_output_bytes,
        )

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
