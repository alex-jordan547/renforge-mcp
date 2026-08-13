"""Cohesive domain registration for RenForge's public MCP tools."""

from __future__ import annotations

from typing import Any

from ..tool_definitions import TOOL_DEFINITIONS
from . import content_build, inspection, interaction, lifecycle, project_analysis, runtime_state, scenarios
from .registry import ToolRegistrar
from .wrappers import build_tool_wrappers

DOMAIN_MODULES = (
    project_analysis,
    lifecycle,
    runtime_state,
    interaction,
    inspection,
    scenarios,
    content_build,
)


def register_all_tools(app: Any) -> None:
    tool_decorator = getattr(app, "tool", None)
    if not callable(tool_decorator):
        return

    wrappers = build_tool_wrappers(app)
    registrar = ToolRegistrar(app)
    for domain in DOMAIN_MODULES:
        domain.register(registrar, wrappers)

    expected = set(TOOL_DEFINITIONS)
    if set(wrappers) != expected or registrar.registered_names != expected:
        missing = sorted(expected - registrar.registered_names)
        extra = sorted(registrar.registered_names - expected)
        orphaned = sorted(set(wrappers) - expected)
        raise RuntimeError(
            f"Tool registration drift: missing={missing} extra={extra} orphaned={orphaned}"
        )


__all__ = ["DOMAIN_MODULES", "register_all_tools"]
