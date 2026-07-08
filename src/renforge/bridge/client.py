"""RenForge TCP bridge client primitives."""

from __future__ import annotations

import base64
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BridgeError(RuntimeError):
    """Base error for bridge client failures."""


class BridgeProtocolError(BridgeError):
    """Raised when the bridge response is malformed or invalid."""


@dataclass
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 0
    token: str = ""
    timeout: float = 5.0


class BridgeClient:
    """Client speaking one-request-per-connection newline-delimited JSON."""

    def __init__(self, config: BridgeConfig):
        self._config = config

    @classmethod
    def from_project(cls, project_root: str | Path, *, timeout: float = 5.0) -> "BridgeClient":
        """Build a client from ``<project_root>/.renforge/bridge.json``.

        The running bridge publishes its host/port/token there on startup.
        """
        info_path = Path(project_root) / ".renforge" / "bridge.json"
        data = json.loads(info_path.read_text(encoding="utf-8"))
        return cls(
            BridgeConfig(
                host=str(data.get("host", "127.0.0.1")),
                port=int(data["port"]),
                token=str(data.get("token", "")),
                timeout=timeout,
            )
        )

    def request(self, command: str, payload: dict[str, Any] | None = None) -> dict:
        body = {
            "token": self._config.token,
            "command": command,
            "payload": payload,
        }

        with socket.create_connection(
            (self._config.host, self._config.port), timeout=self._config.timeout
        ) as sock:
            sock.settimeout(self._config.timeout)
            payload_bytes = (json.dumps(body) + "\n").encode("utf-8")

            try:
                sock.sendall(payload_bytes)
            except OSError as exc:
                raise BridgeError(f"bridge send failed: {exc}") from exc

            file_obj = sock.makefile("r", encoding="utf-8")
            try:
                response_line = file_obj.readline()
            except OSError as exc:
                raise BridgeError(f"bridge read failed: {exc}") from exc

        if not response_line:
            raise BridgeProtocolError("bridge response was empty")

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError("bridge response is not valid JSON") from exc

        if not isinstance(response, dict):
            raise BridgeProtocolError("bridge response must be a JSON object")

        return response

    def _checked(self, command: str, payload: dict[str, Any] | None = None) -> dict:
        reply = self.request(command, payload)
        if reply.get("error") is not None:
            raise BridgeError(f"bridge error on '{command}': {reply['error']}")
        return reply

    def ping(self) -> dict:
        return self.request("ping")

    def get_state(self) -> dict:
        return self._checked("get_state")

    def eval_expr(self, expr: str) -> Any:
        return self._checked("eval", {"expr": expr})["value"]

    def get_var(self, name: str) -> Any:
        return self._checked("get_var", {"name": name})["value"]

    def set_var(self, name: str, value: Any) -> dict:
        return self._checked("set_var", {"name": name, "value": value})

    def screenshot(self, width: int = 0, height: int = 0) -> bytes:
        """Return the current game frame as PNG bytes."""
        reply = self._checked("screenshot", {"width": width, "height": height})
        encoded = reply.get("base64")
        if not encoded:
            raise BridgeProtocolError("screenshot reply missing 'base64' data")
        return base64.b64decode(encoded)

    def advance(self) -> dict:
        """Advance the current dialogue (posts a 'dismiss' event)."""
        return self._checked("advance")

    def control(self, action: str) -> dict:
        """Run a named runtime control action inside the Ren'Py bridge."""
        return self._checked("control", {"action": action})

    def poll_events(self, since: int = 0) -> dict:
        """Return pushed events with ``seq > since`` plus the current cursor.

        Reply shape: ``{"events": [...], "cursor": <int>}``.
        """
        return self._checked("poll_events", {"since": since})

    def list_choices(self) -> list[dict[str, Any]]:
        """Return the on-screen focusable choices as ``[{"index", "text"}, ...]``."""
        return self._checked("list_choices")["choices"]

    def select_choice(self, text: str | None = None, index: int | None = None) -> dict:
        """Select a menu option by visible text (preferred) or by index."""
        return self._checked("select_choice", {"text": text, "index": index})
