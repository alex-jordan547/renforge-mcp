from __future__ import annotations

from renforge.bridge.client import BridgeError
from renforge.tools import live


def test_wait_until_matches_label_and_returns_final_state(monkeypatch, tmp_path):
    states = iter(
        [
            {"current_label": "start", "menu": False, "variables": {"score": 1}},
            {"current_label": "chapter_two", "menu": True, "variables": {"score": 2}},
        ]
    )

    class FakeClient:
        def get_state(self):
            return next(states)

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.wait_until(
        str(tmp_path), label="chapter_two", timeout=1.0, interval=0
    )

    assert result["ok"] is True
    assert result["matched"] == {"type": "label", "value": "chapter_two"}
    assert result["state"]["current_label"] == "chapter_two"
    assert result["state"]["menu"] is True
    assert result["state_profile"] == "interaction"
    assert "variables" not in result["state"] or "score" not in result["state"].get("variables", {})
    assert 0 <= result["elapsed"] < 1.0


def test_wait_until_full_profile_keeps_variables(monkeypatch, tmp_path):
    class FakeClient:
        def get_state(self):
            return {"current_label": "done", "menu": False, "variables": {"score": 9}}

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.wait_until(
        str(tmp_path),
        label="done",
        timeout=0,
        interval=0,
        state_profile="full",
    )
    assert result["ok"] is True
    assert result["state"]["variables"]["score"] == 9


def test_wait_until_times_out_with_final_state(monkeypatch, tmp_path):
    class FakeClient:
        def get_state(self):
            return {"current_label": "start", "menu": False}

        def eval_expr(self, expression):
            assert expression == "renpy.get_screen('dialogue') is not None"
            return False

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.wait_until(
        str(tmp_path), screen="dialogue", timeout=0, interval=0
    )

    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert result["state"]["current_label"] == "start"
    assert result["state"]["menu"] is False
    assert result["elapsed"] >= 0


def test_wait_until_returns_clean_bridge_error_on_disconnect(monkeypatch, tmp_path):
    class FakeClient:
        calls = 0

        def get_state(self):
            self.calls += 1
            if self.calls == 1:
                return {"current_label": "start"}
            raise BridgeError("bridge disconnected")

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.wait_until(
        str(tmp_path), label="chapter_two", timeout=1.0, interval=0
    )

    assert result == {"ok": False, "error": "bridge disconnected"}


def test_wait_until_requires_exactly_one_condition_and_valid_numbers(tmp_path):
    assert live.wait_until(str(tmp_path), timeout=0, interval=0) == {
        "ok": False,
        "error": "exactly one of label, screen, expr is required",
    }
    assert live.wait_until(str(tmp_path), label="a", screen="b") == {
        "ok": False,
        "error": "exactly one of label, screen, expr is required",
    }
    assert live.wait_until(str(tmp_path), label="a", timeout=-1) == {
        "ok": False,
        "error": "timeout must be between 0 and 120 seconds",
    }
    assert live.wait_until(str(tmp_path), label="a", interval=-1) == {
        "ok": False,
        "error": "interval must be a finite non-negative number",
    }


def test_wait_until_yields_when_interval_is_zero(monkeypatch, tmp_path):
    sleeps = []

    class FakeClient:
        def get_state(self):
            return {"current_label": "start"}

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())
    monkeypatch.setattr(live.time, "sleep", lambda duration: sleeps.append(duration))

    result = live.wait_until(str(tmp_path), label="never", timeout=0.01, interval=0)

    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert sleeps
    assert all(0 < duration <= 0.001 for duration in sleeps)
