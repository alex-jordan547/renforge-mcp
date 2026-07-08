from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol, Set


class WebSocketHub:
    class _WebSocket(Protocol):
        async def accept(self) -> None:
            ...

        async def send_text(self, text: str) -> None:
            ...

    def __init__(self) -> None:
        self._connections: Set[WebSocketHub._WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, socket: WebSocketHub._WebSocket) -> None:
        await socket.accept()
        async with self._lock:
            self._connections.add(socket)

    async def disconnect(self, socket: WebSocketHub._WebSocket) -> None:
        async with self._lock:
            self._connections.discard(socket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._connections:
            return

        text = json.dumps(payload)
        async with self._lock:
            sockets = list(self._connections)

        for socket in sockets:
            try:
                await socket.send_text(text)
            except Exception:
                await self.disconnect(socket)


def build_ws_envelope(
    *,
    kind: str,
    type: str,
    payload: dict[str, Any],
    timestamp: int | None = None,
) -> dict[str, Any]:
    value = timestamp
    if value is None:
        value = int(time.time() * 1000)
    return {"kind": kind, "type": type, "timestamp": value, "payload": payload}
