from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Union

DATA_ENCODING: str = "utf-8"
FILE_MODE: int = 0o644


def _write_atomic_chunks(
    path: str | os.PathLike[str],
    chunks: Iterable[str | bytes],
    *,
    encoding: str,
    mode: int,
    follow_symlinks: bool,
    max_bytes: int | None = None,
) -> None:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if follow_symlinks:
        destination = destination.resolve()
    elif destination.is_symlink():
        raise ValueError("atomic-write destination must not be a symlink: %s" % destination)

    fd, temp_name = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=str(destination.parent))
    try:
        written = 0
        with os.fdopen(fd, "wb") as handle:
            for chunk in chunks:
                encoded = chunk.encode(encoding) if isinstance(chunk, str) else chunk
                written += len(encoded)
                if max_bytes is not None and written > max_bytes:
                    raise ValueError("atomic-write payload exceeds %d bytes" % max_bytes)
                handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
        if not hasattr(os, "fchmod"):
            os.chmod(temp_name, mode)
        if not follow_symlinks and destination.is_symlink():
            raise ValueError("atomic-write destination must not be a symlink: %s" % destination)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def write_atomic(
    path: str | os.PathLike[str],
    data: Union[str, bytes],
    *,
    encoding: str = DATA_ENCODING,
    mode: int = FILE_MODE,
    follow_symlinks: bool = True,
) -> None:
    _write_atomic_chunks(
        path,
        (data,),
        encoding=encoding,
        mode=mode,
        max_bytes=None,
        follow_symlinks=follow_symlinks,
    )


def write_json_atomic(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = DATA_ENCODING,
    mode: int = FILE_MODE,
    follow_symlinks: bool = True,
    max_bytes: int | None = None,
) -> None:
    chunks = json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(data)
    _write_atomic_chunks(
        path,
        chunks,
        encoding=encoding,
        mode=mode,
        follow_symlinks=follow_symlinks,
        max_bytes=max_bytes,
    )
