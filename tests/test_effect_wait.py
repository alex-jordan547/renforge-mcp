from __future__ import annotations

from renforge.effect_wait import (
    event_matches,
    expected_events_for_action,
    wait_for_effect,
)


def test_expected_events_for_control_and_ui_actions():
    assert "quick_save.completed" in expected_events_for_action("quick_save")
    assert "rollback.completed" in expected_events_for_action("Rollback")
    assert "auto.changed" in expected_events_for_action("Preference('auto-forward')")


def test_wait_for_effect_matches_correlated_event():
    events = {
        0: {
            "events": [
                {
                    "seq": 1,
                    "type": "quick_save.completed",
                    "event": "quick_save.completed",
                    "correlation_id": "click-1",
                    "slot": "quick-1",
                }
            ],
            "cursor": 1,
        }
    }

    def poll(since: int):
        return events.get(since, {"events": [], "cursor": since})

    result = wait_for_effect(
        poll,
        since=0,
        interaction_id="click-1",
        expected_types=("quick_save.completed",),
        timeout=0.2,
        interval=0,
    )
    assert result["ok"] is True
    assert result["effect"]["slot"] == "quick-1"


def test_wait_for_effect_times_out():
    result = wait_for_effect(
        lambda _since: {"events": [], "cursor": 0},
        since=0,
        interaction_id="x",
        expected_types=("auto.changed",),
        timeout=0.05,
        interval=0.01,
    )
    assert result["ok"] is False
    assert result["error"] == "effect_timeout"


def test_event_matches_rejects_other_correlation():
    event = {
        "type": "quick_save.completed",
        "correlation_id": "a",
    }
    assert event_matches(event, interaction_id="b", expected_types=["quick_save.completed"]) is False
    assert event_matches(event, interaction_id="a", expected_types=["quick_save.completed"]) is True
