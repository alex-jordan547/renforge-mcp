from __future__ import annotations

from renforge.tools import live


def test_run_scenario_set_wait_assert_happy_path(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        live,
        "set_var",
        lambda path, name, value: calls.append(("set", name, value)) or {"ok": True},
    )
    monkeypatch.setattr(
        live,
        "wait_until",
        lambda path, **kwargs: {
            "ok": True,
            "matched": {"type": "screen", "value": kwargs.get("screen")},
            "state": {"current_label": "show_thought"},
        },
    )
    monkeypatch.setattr(
        live,
        "eval_expr",
        lambda path, expr: {"ok": True, "value": True},
    )

    result = live.run_scenario(
        str(tmp_path),
        name="Skip stops at choices",
        steps=[
            {"set": {"_preferences.skip_unseen": True}},
            {"wait": {"screen": "choice"}},
            {"assert": {"expr": "config.skipping is None", "message": "Skip must stop"}},
        ],
    )

    assert result["ok"] is True
    assert result["scenario"] == "Skip stops at choices"
    assert result["passed"] == 3
    assert result["failed"] == 0
    assert calls == [("set", "_preferences.skip_unseen", True)]


def test_run_scenario_failure_collects_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr(
        live,
        "eval_expr",
        lambda path, expr: {"ok": True, "value": "slow"},
    )
    monkeypatch.setattr(
        live,
        "game_state",
        lambda path, **kwargs: {
            "ok": True,
            "current_label": "show_thought",
            "menu": True,
        },
    )
    monkeypatch.setattr(live, "list_choices", lambda path: {"ok": True, "choices": []})
    monkeypatch.setattr(live, "get_errors", lambda path: {"ok": True, "events": []})
    monkeypatch.setattr(live, "poll_events", lambda path: {"ok": True, "events": []})
    monkeypatch.setattr(live, "screenshot_png", lambda path: b"\x89PNG\r\n")

    result = live.run_scenario(
        str(tmp_path),
        steps=[{"assert": {"expr": "config.skipping is None", "equals": None}}],
        capture_on_failure=True,
    )

    assert result["ok"] is False
    assert result["failed_step"] == 0
    assert "diagnostics" in result["steps"][0] or "screenshot" in result
