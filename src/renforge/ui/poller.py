from __future__ import annotations

import asyncio
from pathlib import Path

from ..bridge.client import BridgeClient
from .ws import WebSocketHub, build_ws_envelope


async def _run_in_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _cycle_changed(state: dict, last_state: dict, events: list[object]) -> bool:
    if state != last_state:
        return True
    return bool(events)


async def poll_bridge(project_root: Path, hub: WebSocketHub, stop_event: asyncio.Event, poll_interval: float = 0.35) -> None:
    """Push live state and narrative events from the game bridge to the WS hub.

    Only lightweight payloads travel over the socket: the state snapshot (when it
    changes) and pushed events (labels, dialogue, exceptions). Screenshots are
    deliberately not streamed here — a composited PNG frame is easily >1 MB and
    ill-suited to a broadcast socket; the dashboard fetches previews on demand
    through the ``/api/screenshot`` HTTP endpoint instead.
    """
    client: BridgeClient | None = None
    cursor = 0
    last_state: dict = {}

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
            if state != last_state:
                await hub.broadcast(
                    build_ws_envelope(kind="bridge", type="state", payload=state)
                )
                last_state = state

            events_payload = await _run_in_thread(client.poll_events, cursor)
            events = events_payload.get("events", [])
            cursor = events_payload.get("cursor", cursor)

            for event in events:
                await hub.broadcast(
                    build_ws_envelope(kind="bridge", type="event", payload=event)
                )

        except Exception:
            client = None
            cursor = 0
            last_state = {}
            await asyncio.sleep(1.0)
            continue

        await asyncio.sleep(poll_interval)
