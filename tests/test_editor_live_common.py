from __future__ import annotations

import renforge.editor_live_common as live_common


def test_focusable_edge_point_avoids_nested_text() -> None:
    assert live_common.focusable_edge_point(
        {"x": 100, "y": 100, "width": 77, "height": 44}
    ) == (110, 142)


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
