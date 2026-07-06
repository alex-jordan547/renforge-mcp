from __future__ import annotations

import asyncio
import json
from typing import Any, Set

from starlette.websockets import WebSocket


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, socket: WebSocket) -> None:
        await socket.accept()
        async with self._lock:
            self._connections.add(socket)

    async def disconnect(self, socket: WebSocket) -> None:
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
