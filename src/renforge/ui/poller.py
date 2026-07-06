from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path

from ..bridge.client import BridgeClient
from .ws import WebSocketHub, build_ws_envelope


async def _run_in_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)


def _cycle_changed(state: dict, last_state: dict, events: list[object]) -> bool:
    if state != last_state:
        return True
    return bool(events)


async def poll_bridge(project_root: Path, hub: WebSocketHub, stop_event: asyncio.Event, poll_interval: float = 0.35) -> None:
    client: BridgeClient | None = None
    cursor = 0
    last_state: dict = {}
    last_screenshot_at = 0.0

    while not stop_event.is_set():
        if client is None:
            try:
                client = await _run_in_thread(BridgeClient.from_project, project_root, timeout=1.0)
                cursor = 0
                continue
            except Exception:
                await asyncio.sleep(1.0)
                continue

        try:
            state = await _run_in_thread(client.get_state)
            state_changed = state != last_state
            if state_changed:
                await hub.broadcast(
                    build_ws_envelope(
                        kind="bridge",
                        type="state",
                        payload=state,
                    )
                )
                last_state = state

            events_payload = await _run_in_thread(client.poll_events, cursor)
            events = events_payload.get("events", [])
            cursor = events_payload.get("cursor", cursor)

            changed = state_changed or bool(events)

            for event in events:
                await hub.broadcast(
                    build_ws_envelope(
                        kind="bridge",
                        type="event",
                        payload=event,
                    )
                )
                changed = True

            if changed and time.monotonic() - last_screenshot_at > 0.2:
                png_bytes = await _run_in_thread(client.screenshot, 0, 0)
                await hub.broadcast(
                    build_ws_envelope(
                        kind="bridge",
                        type="screenshot",
                        payload={
                            "format": "png",
                            "base64": base64.b64encode(png_bytes).decode("ascii"),
                        },
                    )
                )
                last_screenshot_at = time.monotonic()

        except Exception:
            client = None
            cursor = 0
            last_state = {}
            await asyncio.sleep(1.0)
            continue

        await asyncio.sleep(poll_interval)
