"""Operation-level runtime policy for high-risk MCP tool actions.

MCP ``ToolAnnotations`` are static, whole-tool hints. This module classifies
the concrete requested operation from the tool name and validated parameters,
then allows or denies it before the implementation runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

RISK_OBSERVATIONAL = "observational"
RISK_MUTATING = "mutating"
RISK_DESTRUCTIVE = "destructive"
RISK_OPEN_WORLD = "open_world"
RISK_MALFORMED = "malformed"
RISK_UNMANAGED = "unmanaged"

MODE_OFF = "off"
MODE_ENFORCE = "enforce"

_RISK_RANK = {
    RISK_OBSERVATIONAL: 0,
    RISK_MUTATING: 1,
    RISK_DESTRUCTIVE: 2,
    RISK_OPEN_WORLD: 3,
    RISK_MALFORMED: 4,
}

CONTROL_ACTIONS: dict[str, str] = {
    "advance": RISK_MUTATING,
    "rollback": RISK_MUTATING,
    "toggle_skip": RISK_MUTATING,
    "toggle_auto": RISK_MUTATING,
    "toggle_afm": RISK_MUTATING,
    "game_menu": RISK_MUTATING,
    "hide_windows": RISK_MUTATING,
    "quick_save": RISK_MUTATING,
    "restart_interaction": RISK_MUTATING,
    "quick_load": RISK_DESTRUCTIVE,
    "reload_script": RISK_DESTRUCTIVE,
    "quit": RISK_DESTRUCTIVE,
}

SAVES_ACTIONS: dict[str, str] = {
    "list": RISK_OBSERVATIONAL,
    "save": RISK_MUTATING,
    "load": RISK_DESTRUCTIVE,
}

SCENARIO_STEP_RISKS: dict[str, str] = {
    "wait": RISK_OBSERVATIONAL,
    "assert": RISK_OBSERVATIONAL,
    "capture": RISK_OBSERVATIONAL,
    "set": RISK_MUTATING,
    "click": RISK_MUTATING,
    "click_at": RISK_MUTATING,
    "advance": RISK_MUTATING,
    "scroll": RISK_MUTATING,
    "select_choice": RISK_MUTATING,
    "send_input": RISK_MUTATING,
    "save": RISK_MUTATING,
    "eval": RISK_OPEN_WORLD,
    "load": RISK_DESTRUCTIVE,
}

_SENSITIVE_PARAM_KEYS = frozenset({"expr", "steps", "value", "text", "extra_info"})

_REASONS = {
    RISK_OBSERVATIONAL: "This operation only observes the project or running game.",
    RISK_MUTATING: "This operation changes live game state in a recoverable way.",
    RISK_DESTRUCTIVE: (
        "This operation can discard or replace live state (quit, load, or reload)."
    ),
    RISK_OPEN_WORLD: (
        "Arbitrary Python can touch the filesystem, processes, network, and game state."
    ),
    RISK_MALFORMED: "The requested operation could not be classified from the supplied parameters.",
}

_NEXT_STEPS = {
    RISK_DESTRUCTIVE: (
        "Retry with authorize=true, add this operation to RENFORGE_POLICY_ALLOW, "
        "or set RENFORGE_POLICY=off for trusted local automation."
    ),
    RISK_OPEN_WORLD: (
        "Retry with authorize=true, add this operation to RENFORGE_POLICY_ALLOW, "
        "or set RENFORGE_POLICY=off for trusted local automation."
    ),
    RISK_MALFORMED: (
        "Fix the tool parameters so the operation can be classified, then retry. "
        "Unknown actions cannot be authorized."
    ),
}


@dataclass(frozen=True)
class PolicySettings:
    mode: str = MODE_OFF
    allow: frozenset[str] = field(default_factory=frozenset)
    allow_risk: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PolicyDecision:
    operation: str
    risk: str
    decision: str
    mode: str
    reason: str
    next_step: str
    allowed: bool

    def to_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "policy_denied",
            "code": "POLICY_DENIED",
            "policy": {
                "operation": self.operation,
                "risk": self.risk,
                "reason": self.reason,
                "next_step": self.next_step,
                "mode": self.mode,
                "decision": self.decision,
            },
        }

    def log_fields(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "risk": self.risk,
            "decision": self.decision,
            "mode": self.mode,
        }


def redact_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy parameters with sensitive values removed for activity logging."""
    redacted: dict[str, Any] = {}
    for key, value in dict(params or {}).items():
        if key in _SENSITIVE_PARAM_KEYS:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def load_settings(
    project_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> PolicySettings:
    env = environ if environ is not None else os.environ
    file_settings = _load_project_file(project_root)

    raw_mode = env.get("RENFORGE_POLICY")
    if raw_mode is None or raw_mode.strip() == "":
        mode = file_settings.mode
    else:
        mode = _parse_mode(raw_mode, fail_closed=True)

    allow = set(file_settings.allow)
    allow.update(_split_csv(env.get("RENFORGE_POLICY_ALLOW")))
    allow_risk = set(file_settings.allow_risk)
    allow_risk.update(_split_csv(env.get("RENFORGE_POLICY_ALLOW_RISK")))
    return PolicySettings(
        mode=mode,
        allow=frozenset(item for item in allow if item),
        allow_risk=frozenset(item for item in allow_risk if item),
    )


def classify(name: str, params: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """Return ``(operation_id, risk)`` for a validated tool invocation."""
    payload = dict(params or {})
    if name == "renforge_control":
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            return "renforge_control", RISK_MALFORMED
        action = action.strip()
        risk = CONTROL_ACTIONS.get(action)
        if risk is None:
            return f"renforge_control.{action}", RISK_MALFORMED
        return f"renforge_control.{action}", risk
    if name == "renforge_saves":
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            return "renforge_saves", RISK_MALFORMED
        action = action.strip()
        risk = SAVES_ACTIONS.get(action)
        if risk is None:
            return f"renforge_saves.{action}", RISK_MALFORMED
        return f"renforge_saves.{action}", risk
    if name == "renforge_eval":
        expr = payload.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            return "renforge_eval", RISK_MALFORMED
        return "renforge_eval", RISK_OPEN_WORLD
    if name == "renforge_run_scenario":
        return _classify_scenario(payload.get("steps"))
    return name, RISK_UNMANAGED


def evaluate(
    name: str,
    params: Mapping[str, Any] | None = None,
    *,
    project_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    settings: PolicySettings | None = None,
) -> PolicyDecision:
    payload = dict(params or {})
    operation, risk = classify(name, payload)
    resolved = settings or load_settings(
        project_root or payload.get("project_path"),
        environ=environ,
    )
    reason = _REASONS.get(risk, _REASONS[RISK_MALFORMED])
    next_step = _NEXT_STEPS.get(
        risk,
        "No authorization is required for this operation.",
    )
    if resolved.mode == MODE_OFF:
        return PolicyDecision(
            operation=operation,
            risk=risk,
            decision="allow",
            mode=MODE_OFF,
            reason=reason,
            next_step=next_step,
            allowed=True,
        )

    allowed = _is_allowed(operation, risk, payload, resolved)
    return PolicyDecision(
        operation=operation,
        risk=risk,
        decision="allow" if allowed else "deny",
        mode=resolved.mode,
        reason=reason,
        next_step=next_step,
        allowed=allowed,
    )


def _is_allowed(
    operation: str,
    risk: str,
    params: Mapping[str, Any],
    settings: PolicySettings,
) -> bool:
    if risk == RISK_UNMANAGED:
        return True
    if risk in {RISK_OBSERVATIONAL, RISK_MUTATING}:
        return True
    if risk == RISK_MALFORMED:
        return False
    if risk in settings.allow_risk:
        return True
    if operation in settings.allow or operation.split(".", 1)[0] in settings.allow:
        return True
    return _authorize_flag(params.get("authorize")) is True


def _authorize_flag(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return None


def _classify_scenario(steps: Any) -> tuple[str, str]:
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        return "renforge_run_scenario", RISK_MALFORMED
    if not steps:
        return "renforge_run_scenario", RISK_OBSERVATIONAL

    highest = RISK_OBSERVATIONAL
    highest_op = "renforge_run_scenario"
    for step in steps:
        operation, risk = _classify_scenario_step(step)
        if _RISK_RANK[risk] > _RISK_RANK[highest]:
            highest = risk
            highest_op = operation
    return highest_op, highest


def _classify_scenario_step(step: Any) -> tuple[str, str]:
    if not isinstance(step, dict):
        return "renforge_run_scenario", RISK_MALFORMED
    action_keys = tuple(SCENARIO_STEP_RISKS) + ("control",)
    present = [key for key in action_keys if key in step]
    if len(present) != 1:
        return "renforge_run_scenario", RISK_MALFORMED
    action = present[0]
    if action == "control":
        control_action = _control_payload_action(step.get("control"))
        if control_action is None:
            return "renforge_run_scenario.control", RISK_MALFORMED
        risk = CONTROL_ACTIONS.get(control_action)
        if risk is None:
            return f"renforge_run_scenario.control.{control_action}", RISK_MALFORMED
        return f"renforge_run_scenario.control.{control_action}", risk
    if action == "wait":
        payload = step.get("wait")
        if isinstance(payload, dict) and "expr" in payload:
            expr = payload.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                return "renforge_run_scenario.wait", RISK_MALFORMED
            return "renforge_run_scenario.wait", RISK_OPEN_WORLD
    if action == "assert":
        payload = step.get("assert")
        expr = payload.get("expr") if isinstance(payload, dict) else payload
        if not isinstance(expr, str) or not expr.strip():
            return "renforge_run_scenario.assert", RISK_MALFORMED
        return "renforge_run_scenario.assert", RISK_OPEN_WORLD
    return f"renforge_run_scenario.{action}", SCENARIO_STEP_RISKS[action]


def _control_payload_action(payload: Any) -> str | None:
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if isinstance(payload, dict):
        action = payload.get("action")
        if isinstance(action, str) and action.strip():
            return action.strip()
    return None


def _parse_mode(raw: str, *, fail_closed: bool) -> str:
    value = raw.strip().lower()
    if value in {MODE_OFF, MODE_ENFORCE}:
        return value
    if fail_closed:
        return MODE_ENFORCE
    return MODE_OFF


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _load_project_file(project_root: str | Path | None) -> PolicySettings:
    if project_root in (None, ""):
        return PolicySettings()
    try:
        root = Path(str(project_root)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return PolicySettings()
    path = root / ".renforge" / "policy.json"
    try:
        if not path.is_file():
            return PolicySettings()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PolicySettings(mode=MODE_ENFORCE)

    if not isinstance(payload, dict):
        return PolicySettings(mode=MODE_ENFORCE)

    raw_mode = payload.get("mode", MODE_OFF)
    if not isinstance(raw_mode, str):
        return PolicySettings(mode=MODE_ENFORCE)
    mode = _parse_mode(raw_mode, fail_closed=True)
    allow = payload.get("allow", [])
    allow_risk = payload.get("allow_risk", [])
    if not isinstance(allow, list) or not isinstance(allow_risk, list):
        return PolicySettings(mode=MODE_ENFORCE)
    if not all(isinstance(item, str) for item in allow + allow_risk):
        return PolicySettings(mode=MODE_ENFORCE)
    return PolicySettings(
        mode=mode,
        allow=frozenset(item.strip() for item in allow if item.strip()),
        allow_risk=frozenset(item.strip() for item in allow_risk if item.strip()),
    )
