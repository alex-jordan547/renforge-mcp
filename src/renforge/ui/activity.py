from __future__ import annotations

import asyncio
from collections import deque
import json
from pathlib import Path
from typing import Any

from .ws import WebSocketHub, build_ws_envelope


def _coerce_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_recent_activity(project_root: Path, limit: int = 20) -> list[dict[str, Any]]:
    path = project_root / ".renforge" / "activity.jsonl"
    if limit <= 0 or not path.exists():
        return []

    events: deque[dict[str, Any]] = deque(maxlen=limit)

    try:
        with path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue

                timestamp = _coerce_timestamp(payload.get("ts"))
                events.append(
                    build_ws_envelope(
                        kind="activity",
                        type="activity",
                        timestamp=timestamp,
                        payload=payload,
                    )
                )
    except Exception:
        return []

    return list(events)


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
                raw_payload = json.loads(line)
                if not isinstance(raw_payload, dict):
                    continue
                payload = dict(raw_payload)
            except Exception:
                continue
            timestamp = int(payload.get("ts", 0) or 0)
            await hub.broadcast(
                build_ws_envelope(
                    kind="activity",
                    type="activity",
                    timestamp=timestamp if timestamp else None,
                    payload=payload,
                )
            )

        await asyncio.sleep(0.4)
