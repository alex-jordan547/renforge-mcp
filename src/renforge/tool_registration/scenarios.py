"""Scenario execution and autopilot MCP tools."""

from __future__ import annotations

from typing import Any

TOOL_NAMES = (
    "renforge_run_scenario",
    "renforge_autopilot",
)


def build_wrappers(context):
    live = context.live
    _log_tool_call = context.log_tool_call

    def renforge_run_scenario(
        project_path: str,
        steps: list[dict[str, Any]],
        name: str = "scenario",
        timeout: float = 30.0,
        stop_on_failure: bool = True,
        state_profile: str = "minimal",
        capture_on_failure: bool = True,
    ) -> dict:
        """Run a multi-step live scenario (click/wait/assert/...) in one call.

        On failure, captures a screenshot and compact diagnostics automatically.
        Supported step actions: set, eval, click, click_at, advance, scroll,
        wait, assert, select_choice, capture, save, load, control, send_input.
        """
        return _log_tool_call(
            name="renforge_run_scenario",
            params={
                "project_path": project_path,
                "name": name,
                "timeout": timeout,
                "stop_on_failure": stop_on_failure,
                "state_profile": state_profile,
                "capture_on_failure": capture_on_failure,
                "steps": steps,
            },
            project_root=project_path,
            fn=live.run_scenario,
            args=(project_path,),
            kwargs={
                "steps": steps,
                "name": name,
                "timeout": timeout,
                "stop_on_failure": stop_on_failure,
                "state_profile": state_profile,
                "capture_on_failure": capture_on_failure,
            },
        )


    def renforge_autopilot(project_path: str, max_runs: int = 16, max_steps: int = 60) -> dict:
        """Auto-play the game across all branches; report label coverage and crashes."""
        return _log_tool_call(
            name="renforge_autopilot",
            params={"project_path": project_path, "max_runs": max_runs, "max_steps": max_steps},
            project_root=project_path,
            fn=live.run_autopilot,
            args=(project_path,),
            kwargs={"max_runs": max_runs, "max_steps": max_steps},
        )


    return {name: value for name, value in locals().items() if name in TOOL_NAMES}


def register(registrar, wrappers) -> None:
    registrar.register_many(wrappers, TOOL_NAMES)
