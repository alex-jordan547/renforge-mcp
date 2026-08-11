from __future__ import annotations

import pytest

import renforge.editor_live_common as live_common


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ({"x": 100, "y": 100, "width": 77, "height": 44}, (110, 142)),
        ({"x": 4, "y": 8, "width": 1, "height": 1}, (4, 8)),
        ({"x": 4, "y": 8, "width": 3, "height": 2}, (6, 8)),
    ],
)
def test_focusable_edge_point_stays_inside_bounds(bounds: dict, expected: tuple[int, int]) -> None:
    assert live_common.focusable_edge_point(bounds) == expected


def test_select_lock_can_target_focusable_edge(monkeypatch) -> None:
    calls = []

    class Client:
        def request(self, name: str, payload: dict) -> dict:
            calls.append((name, payload))
            return {"ok": False, "lock_reason": "CONTAINER_POSITION_UNSUPPORTED"}

    monkeypatch.setattr(
        live_common,
        "wait_bounds",
        lambda *_args, **_kwargs: {"x": 100, "y": 100, "width": 77, "height": 44},
    )

    result = live_common.select_lock(
        Client(),
        "container",
        "CONTAINER_POSITION_UNSUPPORTED",
        fixture_screen="fixture",
        prefer_focusable_edge=True,
    )

    assert result == "CONTAINER_POSITION_UNSUPPORTED"
    assert calls == [("editor_task0_select", {"x": 110, "y": 142})]
