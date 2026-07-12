from __future__ import annotations

from renforge.tools import live


def test_send_input_requires_exactly_one_mode() -> None:
    assert live.send_input("/tmp/game") == {
        "ok": False,
        "error": "exactly one of text, key, or scroll is required",
    }
    assert live.send_input("/tmp/game", text="a", key="enter") == {
        "ok": False,
        "error": "exactly one of text, key, or scroll is required",
    }


def test_send_input_rejects_submit_without_text() -> None:
    assert live.send_input("/tmp/game", key="enter", submit=True) == {
        "ok": False,
        "error": "submit is only valid with text input",
    }


def test_send_input_forwards_one_grouped_request(monkeypatch, tmp_path) -> None:
    calls = []

    class FakeClient:
        def send_input(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "mode": "scroll"}

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())
    result = live.send_input(
        str(tmp_path), scroll={"x": 40, "y": 50, "direction": "down"}
    )

    assert result == {"ok": True, "mode": "scroll"}
    assert calls == [{"text": None, "key": None, "scroll": {"x": 40, "y": 50, "direction": "down"}, "submit": False}]
