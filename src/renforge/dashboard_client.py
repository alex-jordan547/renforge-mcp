"""Private local client for delegating display-bound work to the dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .session_registry import dashboard_connection

_HTTP_TIMEOUT_SECONDS = 45.0


def _dashboard_failure(
    *,
    operation: str,
    message: str,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "ready": False,
        "code": "DASHBOARD_REQUEST_FAILED",
        "operation": operation,
        "via": "dashboard",
        "message": message,
        "error": error or message,
    }


def _request(
    project_path: str,
    route: str,
    *,
    operation: str,
    method: str = "POST",
    body: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Talk to the matching dashboard.

    Returns ``None`` only when no dashboard is registered for this project.
    Once a dashboard is selected, transport/HTTP failures become
    ``DASHBOARD_REQUEST_FAILED`` and must never fall back to a local launch.
    """
    connection = dashboard_connection(project_path)
    if not connection:
        return None
    url = connection.get("url")
    token = connection.get("token")
    selected_project = connection.get("project")
    if not all(isinstance(value, str) and value for value in (url, token, selected_project)):
        return None
    selected_key = os.path.normcase(str(Path(selected_project).expanduser().resolve()))
    requested_key = os.path.normcase(str(Path(project_path).expanduser().resolve()))
    if selected_key != requested_key:
        return None

    endpoint = urljoin(url.rstrip("/") + "/", route)
    endpoint = f"{endpoint}?{urlencode({'token': token})}"
    data = None
    headers = {}
    if method.upper() != "GET":
        data = json.dumps(body or {}).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(endpoint, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw.strip() else {}
    except HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            body_text = exc.read().decode("utf-8", "replace")
            if body_text:
                detail = f"{detail}: {body_text[:300]}"
        except Exception:
            pass
        return _dashboard_failure(
            operation=operation,
            message=f"Dashboard {operation} failed ({detail}).",
            error=detail,
        )
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return _dashboard_failure(
            operation=operation,
            message=f"Dashboard {operation} failed: {type(exc).__name__}: {exc}",
            error=f"{type(exc).__name__}: {exc}",
        )
    if not isinstance(payload, dict):
        return _dashboard_failure(
            operation=operation,
            message=f"Dashboard {operation} returned a non-object payload.",
        )
    payload.setdefault("via", "dashboard")
    if payload.get("ok") is False:
        payload.setdefault("ready", False)
        payload.setdefault("code", "DASHBOARD_REQUEST_FAILED")
        payload.setdefault("operation", operation)
    return payload


def launch_game(
    project_path: str,
    *,
    version: str = "stable",
    warp: str | None = None,
    editor: bool = True,
    display: str = "auto",
    audio: str = "auto",
    savedir: str | None = None,
    persistent: str = "existing",
    cleanup_on_stop: bool = True,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """Launch through the matching dashboard, or return ``None`` when none is registered."""
    body: dict[str, Any] = {
        "version": version,
        "warp": warp,
        "editor": True,
        "display": display,
        "audio": audio,
        "savedir": savedir,
        "persistent": persistent,
        "cleanup_on_stop": cleanup_on_stop,
    }
    if timeout is not None:
        body["timeout"] = timeout
    return _request(
        project_path,
        "api/live/launch",
        operation="launch",
        method="POST",
        body=body,
    )


def launch_status(project_path: str) -> dict[str, Any] | None:
    """Poll launch status through the matching dashboard."""
    return _request(
        project_path,
        "api/live/status",
        operation="status",
        method="GET",
    )


def stop_game(project_path: str) -> dict[str, Any] | None:
    """Stop through the matching dashboard, or return ``None`` when none is registered."""
    return _request(
        project_path,
        "api/live/stop",
        operation="stop",
        method="POST",
        body={},
    )


__all__ = ["launch_game", "launch_status", "stop_game"]
