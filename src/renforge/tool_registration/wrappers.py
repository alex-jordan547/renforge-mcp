"""Shared context and assembly for domain-specific MCP wrappers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from ..tool_definitions import TOOL_DEFINITIONS


def _log_tool_call(
    *,
    name: str,
    params: dict[str, Any],
    project_root: str | None,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    from .. import policy

    decision = policy.evaluate(name, params, project_root=project_root)
    if not decision.allowed:
        result = decision.to_result()
        _record_tool_activity(
            project_root,
            name,
            policy.redact_params(params),
            0.0,
            result,
            policy=decision.log_fields(),
        )
        return result

    started = perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    duration_ms = round((perf_counter() - started) * 1000, 2)
    definition = TOOL_DEFINITIONS.get(name)
    should_log = definition is None or not definition.annotations["readOnlyHint"]
    if should_log:
        logged_params = params
        if decision.risk in {policy.RISK_OPEN_WORLD, policy.RISK_DESTRUCTIVE}:
            logged_params = policy.redact_params(params)
        _record_tool_activity(
            project_root,
            name,
            logged_params,
            duration_ms,
            result,
            policy=decision.log_fields(),
        )

    return result


def _record_tool_activity(
    project_root: str | None,
    name: str,
    params: dict[str, Any],
    duration_ms: float,
    result: Any,
    policy: dict[str, Any] | None = None,
) -> None:
    if project_root is None:
        return
    from .. import activity_log

    summary = activity_log.summarize_result(result)
    try:
        activity_log.log_tool_call(
            project_root,
            name,
            params,
            duration_ms,
            result,
            files_touched=summary["files_touched"],
            policy=policy,
        )
    except Exception:
        pass


def _png_content(png: bytes) -> Any:
    from mcp.types import ImageContent

    return ImageContent(
        type="image",
        data=base64.b64encode(png).decode("ascii"),
        mimeType="image/png",
    )


@dataclass(frozen=True)
class ToolWrapperContext:
    app: Any
    live: Any
    project_ops: Any
    inspect_project: Callable[..., Any]
    parse_lint_text: Callable[..., Any]
    scan_project_index: Callable[..., Any]
    log_tool_call: Callable[..., Any] = _log_tool_call
    png_content: Callable[..., Any] = _png_content


def build_tool_wrappers(app: Any) -> dict[str, Callable[..., Any]]:
    from ..tools import live, project_ops
    from ..tools.static import inspect_project, parse_lint_text, scan_project_index
    from . import content_build, inspection, interaction, lifecycle, project_analysis, runtime_state, scenarios

    context = ToolWrapperContext(
        app=app,
        live=live,
        project_ops=project_ops,
        inspect_project=inspect_project,
        parse_lint_text=parse_lint_text,
        scan_project_index=scan_project_index,
    )
    wrappers: dict[str, Callable[..., Any]] = {}
    for domain in (
        project_analysis,
        lifecycle,
        runtime_state,
        interaction,
        inspection,
        scenarios,
        content_build,
    ):
        domain_wrappers = domain.build_wrappers(context)
        duplicates = set(wrappers) & set(domain_wrappers)
        if duplicates:
            raise RuntimeError(f"Duplicate tool wrappers: {sorted(duplicates)}")
        wrappers.update(domain_wrappers)
    return wrappers
