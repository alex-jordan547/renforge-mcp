"""Runtime state and variable MCP tools."""

from __future__ import annotations

import math
from typing import Any

TOOL_NAMES = (
    "renforge_game_state",
    "renforge_game_state_compact",
    "renforge_inspect_screen",
    "renforge_eval",
    "renforge_set_var",
    "renforge_get_var",
    "renforge_poll_events",
    "renforge_get_errors",
    "renforge_wait_until",
)


def build_wrappers(context):
    live = context.live
    _log_tool_call = context.log_tool_call

    def renforge_game_state(
        project_path: str,
        include: list[str] | None = None,
    ) -> dict:
        """Return complete live state; optionally include compact metrics or audio."""
        def _state() -> dict:
            # Preserve the no-payload wire shape for existing callers.
            if include is None:
                return live.game_state(project_path)
            return live.game_state(project_path, include=include)

        return _log_tool_call(
            name="renforge_game_state",
            params={"project_path": project_path, "include": include},
            project_root=project_path,
            fn=_state,
            args=(),
            kwargs={},
        )


    def renforge_game_state_compact(
        project_path: str,
        variable_names: list[str] | None = None,
        variable_prefix: str = "",
        state_profile: str = "interaction",
        max_depth: int = 3,
        max_items: int = 50,
        max_output_bytes: int = 8192,
    ) -> dict:
        """Return bounded live state, optionally with selected variables.

        Defaults to state_profile='interaction' so the full store is never
        returned in the payload unless state_profile='full' is requested.
        """
        def _state() -> dict:
            from ..state_compact import (
                apply_serialization_limits,
                compact_state,
                normalize_state_profile,
                validate_limit_args,
            )

            state = live.game_state(project_path)
            if not state.get("ok"):
                return state
            profile = normalize_state_profile(state_profile, default="interaction")
            if isinstance(profile, dict):
                return profile
            limits = validate_limit_args(
                max_depth=max_depth,
                max_items=max_items,
                max_output_bytes=max_output_bytes,
            )
            if isinstance(limits, dict):
                return limits
            depth, items, budget = limits

            variables = state.get("variables", {})
            if not isinstance(variables, dict):
                variables = {}
            variable_count = len(variables)
            requested = set(variable_names or [])
            if requested or variable_prefix:
                variables = {
                    name: value
                    for name, value in variables.items()
                    if name in requested or (variable_prefix and name.startswith(variable_prefix))
                }
            # Rebuild a state object for compact_state with the filtered vars.
            source = dict(state)
            source["variables"] = variables
            include = list(variables) if (requested or variable_prefix) else list(variable_names or [])
            compacted = compact_state(
                source,
                profile=profile if profile != "full" else "full",
                include=include or None,
                max_depth=depth,
                max_items=items,
                max_output_bytes=budget,
            )
            result = {"ok": True, "state_profile": profile, "variable_count": variable_count}
            result.update(compacted)
            result["variable_count"] = variable_count
            limited_result = apply_serialization_limits(
                result,
                max_depth=depth,
                max_items=items,
                max_output_bytes=budget,
            )
            return limited_result if isinstance(limited_result, dict) else {"value": limited_result}

        return _log_tool_call(
            name="renforge_game_state_compact",
            params={
                "project_path": project_path,
                "variable_names": variable_names,
                "variable_prefix": variable_prefix,
                "state_profile": state_profile,
                "max_depth": max_depth,
                "max_items": max_items,
                "max_output_bytes": max_output_bytes,
            },
            project_root=project_path,
            fn=_state,
            args=(),
            kwargs={},
        )


    def renforge_inspect_screen(project_path: str, name: str) -> dict:
        """Inspect an active screen's layer, JSON-safe scope, and arguments."""
        return _log_tool_call(
            name="renforge_inspect_screen",
            params={"project_path": project_path, "name": name},
            project_root=project_path,
            fn=live.inspect_screen,
            args=(project_path, name),
            kwargs={},
        )


    def renforge_eval(project_path: str, expr: str, authorize: bool = False) -> dict:
        """Evaluate a Python expression in the running game's store namespace.

        This open-world operation requires authorize=true when
        RENFORGE_POLICY=enforce unless it is allowlisted.
        """
        return _log_tool_call(
            name="renforge_eval",
            params={
                "project_path": project_path,
                "expr": expr,
                "authorize": authorize,
            },
            project_root=project_path,
            fn=live.eval_expr,
            args=(project_path, expr),
            kwargs={},
        )


    def renforge_set_var(project_path: str, name: str, value: Any) -> dict:
        """Set a variable in the running game's store namespace."""
        return _log_tool_call(
            name="renforge_set_var",
            params={"project_path": project_path, "name": name, "value": value},
            project_root=project_path,
            fn=live.set_var,
            args=(project_path, name, value),
            kwargs={},
        )


    def renforge_get_var(project_path: str, name: str) -> dict:
        """Read a variable from the running game's store."""
        return _log_tool_call(
            name="renforge_get_var",
            params={"project_path": project_path, "name": name},
            project_root=project_path,
            fn=live.get_var,
            args=(project_path, name),
            kwargs={},
        )


    def renforge_poll_events(project_path: str, since: int = 0) -> dict:
        """Return pushed events (dialogue, labels, exceptions) newer than `since`."""
        return _log_tool_call(
            name="renforge_poll_events",
            params={"project_path": project_path, "since": since},
            project_root=project_path,
            fn=live.poll_events,
            args=(project_path,),
            kwargs={"since": since},
        )


    def renforge_get_errors(project_path: str, since: int = 0) -> dict:
        """Return recent bridge exceptions or bounded crash-file diagnostics."""
        return _log_tool_call(
            name="renforge_get_errors",
            params={"project_path": project_path, "since": since},
            project_root=project_path,
            fn=live.get_errors,
            args=(project_path,),
            kwargs={"since": since},
        )


    def renforge_wait_until(
        project_path: str,
        label: str | None = None,
        screen: str | None = None,
        expr: str | None = None,
        timeout: float = 30.0,
        interval: float = 0.5,
        state_profile: str = "interaction",
        include: list[str] | None = None,
        max_depth: int = 3,
        max_items: int = 50,
        max_output_bytes: int = 8192,
    ) -> dict:
        """Wait for exactly one label, screen, or expression condition.

        Returns a compact state by default (state_profile='interaction').
        Pass include for extra fields/variables; use state_profile='full'
        only when the complete store is required.
        """
        def _wait() -> dict:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                return {"ok": False, "error": "timeout must be a finite non-negative number"}
            if not math.isfinite(float(timeout)) or timeout < 0:
                return {"ok": False, "error": "timeout must be a finite non-negative number"}
            if timeout > 120:
                return {"ok": False, "error": "timeout must be <= 120 seconds"}
            return live.wait_until(
                project_path,
                label=label,
                screen=screen,
                expr=expr,
                timeout=timeout,
                interval=interval,
                state_profile=state_profile,
                include=include,
                max_depth=max_depth,
                max_items=max_items,
                max_output_bytes=max_output_bytes,
            )

        return _log_tool_call(
            name="renforge_wait_until",
            params={
                "project_path": project_path,
                "label": label,
                "screen": screen,
                "expr": expr,
                "timeout": timeout,
                "interval": interval,
                "state_profile": state_profile,
                "include": include,
                "max_depth": max_depth,
                "max_items": max_items,
                "max_output_bytes": max_output_bytes,
            },
            project_root=project_path,
            fn=_wait,
            args=(),
            kwargs={},
        )


    return {name: value for name, value in locals().items() if name in TOOL_NAMES}


def register(registrar, wrappers) -> None:
    registrar.register_many(wrappers, TOOL_NAMES)
