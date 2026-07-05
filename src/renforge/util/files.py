from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union

DATA_ENCODING: str = "utf-8"
FILE_MODE: int = 0o644


def write_atomic(path: str | os.PathLike[str], data: Union[str, bytes], *, encoding: str = DATA_ENCODING, mode: int = FILE_MODE) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = destination.expanduser().resolve()

    if isinstance(data, str):
        payload = data.encode(encoding)
    else:
        payload = data

    fd, temp_name = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_name, destination)
        os.chmod(destination, mode)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)
