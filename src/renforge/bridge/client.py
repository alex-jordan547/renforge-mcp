"""RenForge TCP bridge client primitives."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
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

    def ping(self) -> dict:
        return self.request("ping")
