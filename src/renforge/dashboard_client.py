"""Private local client for delegating display-bound work to the dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .session_registry import dashboard_connection


def _post(project_path: str, route: str, body: dict[str, Any]) -> dict[str, Any] | None:
    connection = dashboard_connection()
    if not connection:
        return None
    url = connection.get("url")
    token = connection.get("token")
    selected_project = connection.get("project")
    if not all(isinstance(value, str) and value for value in (url, token, selected_project)):
        return None
    if Path(selected_project).expanduser().resolve() != Path(project_path).expanduser().resolve():
        return None

    endpoint = urljoin(url.rstrip("/") + "/", route)
    endpoint = f"{endpoint}?{urlencode({'token': token})}"
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload.setdefault("via", "dashboard")
    return payload


def launch_game(
    project_path: str,
    *,
    version: str = "stable",
    warp: str | None = None,
) -> dict[str, Any] | None:
    """Launch through the active dashboard, or return ``None`` when unavailable."""
    return _post(
        project_path,
        "api/live/launch",
        {"version": version, "warp": warp},
    )


def stop_game(project_path: str) -> dict[str, Any] | None:
    """Stop through the active dashboard, or return ``None`` when unavailable."""
    return _post(project_path, "api/live/stop", {})


__all__ = ["launch_game", "stop_game"]
