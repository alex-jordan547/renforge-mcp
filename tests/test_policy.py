import asyncio
import json

import pytest

from renforge.policy import (
    MODE_ENFORCE,
    MODE_OFF,
    RISK_DESTRUCTIVE,
    RISK_MALFORMED,
    RISK_MUTATING,
    RISK_OBSERVATIONAL,
    RISK_OPEN_WORLD,
    classify,
    evaluate,
    load_settings,
    redact_params,
)
from renforge.server import create_app


def test_classify_control_saves_eval_and_scenario_operations() -> None:
    assert classify("renforge_control", {"action": "advance"}) == (
        "renforge_control.advance",
        RISK_MUTATING,
    )
    assert classify("renforge_control", {"action": "quit"}) == (
        "renforge_control.quit",
        RISK_DESTRUCTIVE,
    )
    assert classify("renforge_saves", {"action": "list"}) == (
        "renforge_saves.list",
        RISK_OBSERVATIONAL,
    )
    assert classify("renforge_saves", {"action": "load"}) == (
        "renforge_saves.load",
        RISK_DESTRUCTIVE,
    )
    assert classify("renforge_eval", {"expr": "renpy.version"}) == (
        "renforge_eval",
        RISK_OPEN_WORLD,
    )
    assert classify(
        "renforge_run_scenario",
        {"steps": [{"wait": {"label": "start"}}, {"eval": "True"}]},
    ) == ("renforge_run_scenario.eval", RISK_OPEN_WORLD)
    assert classify(
        "renforge_run_scenario",
        {"steps": [{"wait": {"expr": "renpy.version"}}]},
    ) == ("renforge_run_scenario.wait", RISK_OPEN_WORLD)
    assert classify(
        "renforge_run_scenario",
        {"steps": [{"assert": {"expr": "renpy.version"}}]},
    ) == ("renforge_run_scenario.assert", RISK_OPEN_WORLD)
    assert classify(
        "renforge_run_scenario",
        {"steps": [{"control": {"action": "quit"}}]},
    ) == ("renforge_run_scenario.control.quit", RISK_DESTRUCTIVE)


def test_classify_malformed_operations() -> None:
    assert classify("renforge_control", {"action": "explode"})[1] == RISK_MALFORMED
    assert classify("renforge_saves", {"action": "delete"})[1] == RISK_MALFORMED
    assert classify("renforge_eval", {"expr": "   "})[1] == RISK_MALFORMED
    assert classify("renforge_run_scenario", {"steps": "nope"})[1] == RISK_MALFORMED
    assert classify("renforge_run_scenario", {"steps": [{"wait": {}, "eval": "1"}]})[1] == (
        RISK_MALFORMED
    )


def test_policy_disabled_allows_high_risk_operations(monkeypatch) -> None:
    monkeypatch.setenv("RENFORGE_POLICY", "off")
    decision = evaluate("renforge_eval", {"expr": "1+1", "project_path": "/tmp/game"})
    assert decision.allowed is True
    assert decision.mode == MODE_OFF
    assert decision.decision == "allow"
    assert decision.risk == RISK_OPEN_WORLD


def test_enforce_denies_open_world_and_destructive_without_authorize(monkeypatch) -> None:
    monkeypatch.setenv("RENFORGE_POLICY", "enforce")
    denied_eval = evaluate("renforge_eval", {"expr": "1+1"})
    denied_quit = evaluate("renforge_control", {"action": "quit"})
    allowed_advance = evaluate("renforge_control", {"action": "advance"})
    allowed_list = evaluate("renforge_saves", {"action": "list"})

    assert denied_eval.allowed is False
    assert denied_eval.to_result()["code"] == "POLICY_DENIED"
    assert denied_eval.to_result()["policy"]["operation"] == "renforge_eval"
    assert "authorize=true" in denied_eval.next_step
    assert denied_quit.allowed is False
    assert allowed_advance.allowed is True
    assert allowed_list.allowed is True


def test_enforce_allows_authorized_and_allowlisted_calls(monkeypatch) -> None:
    monkeypatch.setenv("RENFORGE_POLICY", "enforce")
    authorized = evaluate("renforge_eval", {"expr": "1+1", "authorize": True})
    assert authorized.allowed is True

    monkeypatch.setenv("RENFORGE_POLICY_ALLOW", "renforge_control.quit,renforge_saves.load")
    assert evaluate("renforge_control", {"action": "quit"}).allowed is True
    assert evaluate("renforge_saves", {"action": "load", "slot": "a"}).allowed is True
    assert evaluate("renforge_eval", {"expr": "1+1"}).allowed is False


def test_malformed_operations_are_denied_even_with_authorize(monkeypatch) -> None:
    monkeypatch.setenv("RENFORGE_POLICY", "enforce")
    decision = evaluate(
        "renforge_control",
        {"action": "not-an-action", "authorize": True},
    )
    assert decision.allowed is False
    assert decision.risk == RISK_MALFORMED
    assert "cannot be authorized" in decision.next_step


def test_unknown_policy_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("RENFORGE_POLICY", "enforece")
    settings = load_settings()
    assert settings.mode == MODE_ENFORCE
    assert evaluate("renforge_eval", {"expr": "1"}, settings=settings).allowed is False


def test_project_policy_file_and_env_override(tmp_path, monkeypatch) -> None:
    policy_dir = tmp_path / ".renforge"
    policy_dir.mkdir()
    (policy_dir / "policy.json").write_text(
        json.dumps({"mode": "enforce", "allow": ["renforge_eval"]}),
        encoding="utf-8",
    )
    monkeypatch.delenv("RENFORGE_POLICY", raising=False)
    monkeypatch.delenv("RENFORGE_POLICY_ALLOW", raising=False)

    from_file = load_settings(tmp_path)
    assert from_file.mode == MODE_ENFORCE
    assert "renforge_eval" in from_file.allow
    assert evaluate("renforge_eval", {"expr": "1", "project_path": str(tmp_path)}).allowed is True

    monkeypatch.setenv("RENFORGE_POLICY", "off")
    overridden = load_settings(tmp_path)
    assert overridden.mode == MODE_OFF


def test_malformed_policy_file_fails_closed(tmp_path, monkeypatch) -> None:
    policy_dir = tmp_path / ".renforge"
    policy_dir.mkdir()
    (policy_dir / "policy.json").write_text("{not json", encoding="utf-8")
    monkeypatch.delenv("RENFORGE_POLICY", raising=False)
    settings = load_settings(tmp_path)
    assert settings.mode == MODE_ENFORCE


def test_redact_params_strips_sensitive_values() -> None:
    redacted = redact_params({"expr": "secret()", "action": "eval", "steps": [{"eval": "1"}]})
    assert redacted["expr"] == "<redacted>"
    assert redacted["steps"] == "<redacted>"
    assert redacted["action"] == "eval"


def test_denied_eval_never_reaches_implementation(monkeypatch, tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client
    from renforge.tools import live

    monkeypatch.setenv("RENFORGE_POLICY", "enforce")
    calls: list[str] = []
    monkeypatch.setattr(
        live,
        "eval_expr",
        lambda project_path, expr: calls.append(expr) or {"ok": True, "value": expr},
    )

    async def _call(**payload):
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_eval",
                {"project_path": str(tmp_path), **payload},
                raise_on_error=False,
            )

    denied = asyncio.run(_call(expr="1+1"))
    payload = json.loads(next(block.text for block in denied.content if block.type == "text"))
    assert calls == []
    assert payload["ok"] is False
    assert payload["code"] == "POLICY_DENIED"
    assert payload["policy"]["operation"] == "renforge_eval"
    assert payload["policy"]["risk"] == RISK_OPEN_WORLD
    assert "authorize=true" in payload["policy"]["next_step"]

    activity = (tmp_path / ".renforge" / "activity.jsonl").read_text(encoding="utf-8")
    entry = json.loads(activity.strip().splitlines()[-1])
    assert entry["policy"]["decision"] == "deny"
    assert entry["params"]["expr"] == "<redacted>"

    allowed = asyncio.run(_call(expr="1+1", authorize=True))
    allowed_payload = json.loads(
        next(block.text for block in allowed.content if block.type == "text")
    )
    assert calls == ["1+1"]
    assert allowed_payload == {"ok": True, "value": "1+1"}


def test_policy_disabled_eval_reaches_implementation(monkeypatch, tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client
    from renforge.tools import live

    monkeypatch.setenv("RENFORGE_POLICY", "off")
    calls: list[str] = []
    monkeypatch.setattr(
        live,
        "eval_expr",
        lambda project_path, expr: calls.append(expr) or {"ok": True, "value": 2},
    )

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_eval",
                {"project_path": str(tmp_path), "expr": "1+1"},
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert calls == ["1+1"]
    assert payload == {"ok": True, "value": 2}


def test_denied_open_world_scenario_steps_never_run(monkeypatch, tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client
    from renforge.tools import live

    monkeypatch.setenv("RENFORGE_POLICY", "enforce")
    calls: list[object] = []
    monkeypatch.setattr(
        live,
        "run_scenario",
        lambda project_path, **kwargs: calls.append(kwargs) or {"ok": True},
    )

    async def _call(steps, **extra):
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_run_scenario",
                {"project_path": str(tmp_path), "steps": steps, **extra},
                raise_on_error=False,
            )

    for step, operation in (
        ({"eval": "True"}, "renforge_run_scenario.eval"),
        ({"wait": {"expr": "True"}}, "renforge_run_scenario.wait"),
        ({"assert": {"expr": "True"}}, "renforge_run_scenario.assert"),
    ):
        denied = asyncio.run(_call([step]))
        payload = json.loads(
            next(block.text for block in denied.content if block.type == "text")
        )
        assert calls == []
        assert payload["policy"]["operation"] == operation
        assert payload["policy"]["risk"] == RISK_OPEN_WORLD

    allowed = asyncio.run(_call([{"wait": {"label": "start"}}]))
    allowed_payload = json.loads(
        next(block.text for block in allowed.content if block.type == "text")
    )
    assert allowed_payload == {"ok": True}
    assert len(calls) == 1
