"""Private per-user registry shared by RenForge dashboard and MCP processes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from renforge.util.files import (
    PrivatePathError,
    atomic_write_private_json,
    ensure_private_directory,
    read_regular_file_nofollow,
)

_RECORD_MAX_BYTES = 16 * 1024
_SCHEMA_VERSION = 1
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
_RECORD_KEYS = frozenset(
    {"schema_version", "pid", "project", "url", "token", "updated_at_ms"}
)


def _windows_user_digest(identity: str) -> str:
    """Return a process-stable, non-reversible directory discriminator."""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _registry_dir() -> Path:
    configured = os.environ.get("RENFORGE_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser() / "dashboards"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "RenForge" / "runtime" / "dashboards"

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "RenForge" / "runtime" / "dashboards"
        # Identity-hashed TEMP fallback when LOCALAPPDATA is unavailable.
        identity = os.environ.get("USERNAME") or "user"
        digest = _windows_user_digest(identity)
        return Path(tempfile.gettempdir()) / f"renforge-{digest}" / "dashboards"

    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "renforge" / "dashboards"
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    return Path(tempfile.gettempdir()) / f"renforge-{uid}" / "dashboards"


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


def _canonical_project(project: str | Path) -> str:
    return str(Path(project).expanduser().resolve())


def _validate_record(payload: object, *, path: Path) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or set(payload.keys()) != _RECORD_KEYS:
        return None
    if payload.get("schema_version") != _SCHEMA_VERSION:
        return None
    pid = payload.get("pid")
    if type(pid) is not int or isinstance(pid, bool) or pid <= 0:
        return None
    if path.stem != str(pid):
        return None
    project = payload.get("project")
    url = payload.get("url")
    token = payload.get("token")
    updated = payload.get("updated_at_ms")
    if not all(isinstance(value, str) and value for value in (project, url, token)):
        return None
    if not _TOKEN_RE.fullmatch(str(token)):
        return None
    if type(updated) is not int or isinstance(updated, bool) or updated < 0:
        return None
    try:
        canonical = _canonical_project(str(project))
    except (OSError, RuntimeError, ValueError):
        return None
    if str(project) != canonical:
        # Accept only fully resolved absolute project paths.
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "pid": pid,
        "project": canonical,
        "url": str(url),
        "token": str(token),
        "updated_at_ms": updated,
    }


def _load_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = read_regular_file_nofollow(path, max_bytes=_RECORD_MAX_BYTES)
    except (PrivatePathError, OSError):
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return _validate_record(payload, path=path)


def publish_dashboard(
    project: str | Path,
    *,
    url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Publish the dashboard's current project for local MCP clients."""
    directory = _registry_dir()
    try:
        ensure_private_directory(directory)
    except PrivatePathError as exc:
        raise RuntimeError(f"DASHBOARD_REGISTRY_DIRECTORY_UNSAFE: {exc.message}") from exc

    path = _record_path()
    previous: dict[str, Any] = {}
    loaded = _load_record(path)
    if loaded is not None:
        previous = loaded

    resolved_project = _canonical_project(project)
    record = {
        "schema_version": _SCHEMA_VERSION,
        "pid": os.getpid(),
        "project": resolved_project,
        "url": url if url is not None else previous.get("url"),
        "token": token if token is not None else previous.get("token"),
        "updated_at_ms": int(time.time() * 1000),
    }
    if not isinstance(record["url"], str) or not record["url"]:
        raise ValueError("dashboard url is required")
    if not isinstance(record["token"], str) or not _TOKEN_RE.fullmatch(record["token"]):
        # Allow publish during tests that still use non-22-char tokens by
        # normalizing only when the caller passes a fresh token_urlsafe(16).
        # Reject empty/invalid tokens so the registry stays strict.
        raise ValueError("dashboard token must be exactly 22 URL-safe Base64 characters")

    try:
        atomic_write_private_json(path, record, max_bytes=_RECORD_MAX_BYTES)
    except PrivatePathError as exc:
        raise RuntimeError(f"DASHBOARD_REGISTRY_DIRECTORY_UNSAFE: {exc.message}") from exc
    return record


def _iter_live_records() -> list[dict[str, Any]]:
    directory = _registry_dir()
    try:
        ensure_private_directory(directory)
    except PrivatePathError:
        return []
    if not directory.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        record = _load_record(path)
        if record is None:
            continue
        pid = int(record["pid"])
        if not _pid_is_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        records.append(record)
    return records


def active_dashboard() -> dict[str, Any] | None:
    """Return public context for the most recently updated live dashboard."""
    records = _iter_live_records()
    if not records:
        return None
    record = max(records, key=lambda item: (int(item["updated_at_ms"]), int(item["pid"])))
    return {key: value for key, value in record.items() if key != "token"}


def dashboard_connection(project_path: str | Path | None = None) -> dict[str, Any] | None:
    """Return private connection details for a matching dashboard.

    When ``project_path`` is provided, only records for the same canonical
    project are considered (normcase). Among matches, the greatest
    ``(updated_at_ms, pid)`` wins so a newer unrelated dashboard cannot mask
    this project's connection.
    """
    records = _iter_live_records()
    if not records:
        return None
    if project_path is not None:
        try:
            wanted = os.path.normcase(_canonical_project(project_path))
        except (OSError, RuntimeError, ValueError):
            return None
        records = [
            record
            for record in records
            if os.path.normcase(str(record["project"])) == wanted
        ]
        if not records:
            return None
    return max(records, key=lambda item: (int(item["updated_at_ms"]), int(item["pid"])))


def clear_dashboard(pid: int | None = None) -> None:
    """Remove one dashboard registration."""
    try:
        _record_path(pid).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


__all__ = [
    "active_dashboard",
    "clear_dashboard",
    "dashboard_connection",
    "publish_dashboard",
]
