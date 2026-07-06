from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .ws import WebSocketHub


async def tail_activity(project_root: Path, hub: WebSocketHub, stop_event: asyncio.Event) -> None:
    path = project_root / ".renforge" / "activity.jsonl"
    offset = 0

    while not stop_event.is_set():
        if not path.exists():
            await asyncio.sleep(0.8)
            continue

        try:
            size = path.stat().st_size
            if size < offset:
                offset = 0
            with path.open("r", encoding="utf-8") as fp:
                fp.seek(offset)
                lines = fp.readlines()
                offset = fp.tell()
        except Exception:
            await asyncio.sleep(0.8)
            continue

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            await hub.broadcast({"type": "activity", "payload": payload})

        await asyncio.sleep(0.4)
