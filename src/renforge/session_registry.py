"""Small local registry shared by RenForge dashboard and MCP processes."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def _registry_dir() -> Path:
    configured = os.environ.get("RENFORGE_RUNTIME_DIR")
    if configured:
        root = Path(configured).expanduser()
    elif os.environ.get("XDG_RUNTIME_DIR"):
        root = Path(os.environ["XDG_RUNTIME_DIR"])
    else:
        root = Path(tempfile.gettempdir()) / f"renforge-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    return root / "renforge" / "dashboards"


def _record_path(pid: int | None = None) -> Path:
    return _registry_dir() / f"{pid or os.getpid()}.json"


def _pid_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def publish_dashboard(
    project: str | Path,
    *,
    url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Publish the dashboard's current project for local MCP clients."""

    path = _record_path()
    previous: dict[str, Any] = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            previous = {}

    record = {
        "pid": os.getpid(),
        "project": str(Path(project).expanduser().resolve()),
        "url": url if url is not None else previous.get("url"),
        "token": token if token is not None else previous.get("token"),
        "updated_at": int(time.time() * 1000),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)
    return record


def _active_dashboard_record() -> dict[str, Any] | None:

    directory = _registry_dir()
    if not directory.exists():
        return None

    records: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = int(record["pid"])
            project = str(record["project"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if not _pid_is_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        records.append({**record, "pid": pid, "project": project})

    if not records:
        return None
    return max(records, key=lambda item: int(item.get("updated_at", 0)))


def active_dashboard() -> dict[str, Any] | None:
    """Return public context for the most recently updated live dashboard."""

    record = _active_dashboard_record()
    if record is None:
        return None
    return {key: value for key, value in record.items() if key != "token"}


def dashboard_connection() -> dict[str, Any] | None:
    """Return private connection details from the user-only runtime registry."""

    return _active_dashboard_record()


def clear_dashboard(pid: int | None = None) -> None:
    """Remove one dashboard registration."""

    try:
        _record_path(pid).unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "active_dashboard",
    "clear_dashboard",
    "dashboard_connection",
    "publish_dashboard",
]
