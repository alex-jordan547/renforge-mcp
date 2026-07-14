"""Wait for correlated RenForge business events after an interaction."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable

# Maps control actions / UI action names to business event type names.
EFFECT_EVENTS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "quick_save": ("quick_save.completed", "quick_save.failed"),
    "quick_load": ("quick_load.completed", "quick_load.failed"),
    "rollback": ("rollback.completed",),
    "toggle_skip": ("skip.changed", "skip.started", "skip.stopped"),
    "toggle_auto": ("auto.changed",),
    "toggle_afm": ("auto.changed",),
    "save": ("save.completed", "save.failed"),
    "load": ("load.completed",),
}

# Substring matchers for UI element action class names.
_ACTION_NAME_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quicksave", ("quick_save.completed", "quick_save.failed")),
    ("quick_save", ("quick_save.completed", "quick_save.failed")),
    ("quickload", ("quick_load.completed", "quick_load.failed")),
    ("quick_load", ("quick_load.completed", "quick_load.failed")),
    ("rollback", ("rollback.completed",)),
    ("skip", ("skip.changed", "skip.started", "skip.stopped")),
    ("auto", ("auto.changed", "auto.advanced")),
    ("afm", ("auto.changed", "auto.advanced")),
)


def expected_events_for_action(action: str | None) -> tuple[str, ...]:
    if not action:
        return ()
    key = str(action).strip()
    if key in EFFECT_EVENTS_BY_ACTION:
        return EFFECT_EVENTS_BY_ACTION[key]
    lowered = key.casefold()
    for needle, events in _ACTION_NAME_HINTS:
        if needle in lowered:
            return events
    return ()


def event_matches(
    event: dict[str, Any],
    *,
    interaction_id: str | None,
    expected_types: Iterable[str],
) -> bool:
    if not isinstance(event, dict):
        return False
    expected = set(expected_types)
    event_type = str(event.get("type") or event.get("event") or "")
    if expected and event_type not in expected:
        return False
    if interaction_id is not None:
        correlated = event.get("correlation_id") or event.get("interaction_id")
        if correlated is not None and str(correlated) != str(interaction_id):
            return False
    return True


def wait_for_effect(
    poll_events: Callable[[int], dict[str, Any]],
    *,
    since: int,
    interaction_id: str | None,
    expected_types: Iterable[str],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> dict[str, Any]:
    """Poll until a matching business event appears or the timeout elapses."""
    expected = tuple(expected_types)
    if not expected:
        return {
            "ok": False,
            "error": "no expected effect types for this action",
            "interaction_id": interaction_id,
        }

    started = time.monotonic()
    cursor = int(since)
    deadline = started + max(0.0, float(timeout))
    last_events: list[dict[str, Any]] = []

    while True:
        reply = poll_events(cursor)
        events = reply.get("events") if isinstance(reply, dict) else None
        if not isinstance(events, list):
            events = []
        last_events = [e for e in events if isinstance(e, dict)]
        for event in last_events:
            if event_matches(event, interaction_id=interaction_id, expected_types=expected):
                return {
                    "ok": True,
                    "event": event.get("event") or event.get("type"),
                    "effect": event,
                    "interaction_id": interaction_id,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
        if isinstance(reply, dict) and reply.get("cursor") is not None:
            try:
                cursor = int(reply["cursor"])
            except (TypeError, ValueError):
                pass
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "error": "effect_timeout",
                "interaction_id": interaction_id,
                "expected": list(expected),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "recent_events": last_events[-5:],
            }
        time.sleep(max(0.0, float(interval)))


__all__ = [
    "EFFECT_EVENTS_BY_ACTION",
    "event_matches",
    "expected_events_for_action",
    "wait_for_effect",
]
