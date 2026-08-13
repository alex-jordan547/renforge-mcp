"""Project discovery and static-analysis MCP tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import __version__, session_registry
from ..project import discover_project_from

TOOL_NAMES = (
    "renforge_info",
    "renforge_context",
    "renforge_inspect_image",
    "renforge_inspect_project",
    "renforge_scan_project",
    "renforge_find_references",
    "renforge_parse_lint",
)


def build_wrappers(context):
    app = context.app
    live = context.live
    inspect_project = context.inspect_project
    parse_lint_text = context.parse_lint_text
    scan_project_index = context.scan_project_index
    _log_tool_call = context.log_tool_call
    _png_content = context.png_content

    def _context_payload() -> dict[str, Any]:
        dashboard = session_registry.active_dashboard()
        default_project = getattr(app, "project_root", None)
        active_project = dashboard.get("project") if dashboard else None
        project_source = "dashboard" if active_project else None
        if active_project is None and default_project is not None:
            active_project = str(Path(default_project).expanduser().resolve())
            project_source = "serve_default"
        if active_project is None:
            detected = discover_project_from()
            if detected is not None:
                active_project = str(detected)
                project_source = "cwd"
        payload: dict[str, Any] = {
            "ok": True,
            "version": __version__,
            "active_project": active_project,
            "project_source": project_source,
            "dashboard": dashboard,
            "live_editor": {
                "enabled_by_default": True,
                "launch_tool": "renforge_launch",
                "guide": "docs/LIVE_EDITOR.md",
                "summary": (
                    "In-game Live Editor is injected by default on "
                    "renforge_launch. Preview is runtime-only until Save; "
                    "locked targets stay inspectable. Use only public MCP "
                    "tools — never private editor_task0_* handlers."
                ),
                "agent_workflow": [
                    "renforge_info",
                    "renforge_launch",
                    "renforge_launch_status",
                    "renforge_screenshot",
                    "renforge_scene_tree",
                    "renforge_click_at",
                    "renforge_click_element",
                    "renforge_stop",
                ],
            },
        }
        if active_project is None:
            payload["hint"] = (
                "No dashboard, serve default, or Ren'Py project near the "
                "current directory. Every tool accepts project_path directly: "
                "ask the user for the game's path."
            )
        return payload


    def renforge_info() -> dict:
        """Call first: report RenForge version and the active project.

        active_project falls back from the dashboard selection to the serve
        default, then to a Ren'Py project detected from the current directory
        (project_source says which one matched). A null active_project only
        means auto-discovery found nothing — every tool accepts project_path
        directly, so ask the user for the game's path and keep going.
        """
        return _context_payload()


    def renforge_context() -> dict:
        """Discover the active Ren'Py project (dashboard, serve default, or cwd)."""
        return _context_payload()


    def renforge_inspect_image(
        image_path: str,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_width: int = 0,
        crop_height: int = 0,
        scale: float = 1.0,
    ):
        """Open a local image and return an optional cropped/zoomed PNG for inspection."""
        from ..image_ops import inspect_image

        return _log_tool_call(
            name="renforge_inspect_image",
            params={
                "image_path": image_path,
                "crop_x": crop_x,
                "crop_y": crop_y,
                "crop_width": crop_width,
                "crop_height": crop_height,
                "scale": scale,
            },
            project_root=None,
            fn=lambda: _png_content(
                inspect_image(
                    image_path,
                    crop_x=crop_x,
                    crop_y=crop_y,
                    crop_width=crop_width,
                    crop_height=crop_height,
                    scale=scale,
                )
            ),
            args=(),
            kwargs={},
        )


    def renforge_inspect_project(project_path: str) -> dict:
        return _log_tool_call(
            name="renforge_inspect_project",
            params={"project_path": project_path},
            project_root=project_path,
            fn=inspect_project,
            args=(project_path,),
            kwargs={},
        )


    def renforge_scan_project(
        project_path: str,
        sections: list[str] | None = None,
        file_glob: str = "",
        symbol: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> dict:
        """Scan scripts; defaults to summary-only, with opt-in sections and pagination."""
        selected_sections = [] if sections is None else sections
        return _log_tool_call(
            name="renforge_scan_project",
            params={
                "project_path": project_path,
                "sections": selected_sections,
                "file_glob": file_glob,
                "symbol": symbol,
                "offset": offset,
                "limit": limit,
            },
            project_root=project_path,
            fn=scan_project_index,
            args=(project_path,),
            kwargs={
                "sections": selected_sections,
                "file_glob": file_glob,
                "symbol": symbol,
                "offset": offset,
                "limit": limit,
            },
        )


    def renforge_find_references(
        project_path: str,
        symbol: str,
        file_glob: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> dict:
        """Find exact Ren'Py definitions/usages, including text interpolations."""
        from ..symbols import find_references

        return _log_tool_call(
            name="renforge_find_references",
            params={
                "project_path": project_path,
                "symbol": symbol,
                "file_glob": file_glob,
                "offset": offset,
                "limit": limit,
            },
            project_root=project_path,
            fn=find_references,
            args=(project_path, symbol),
            kwargs={"file_glob": file_glob, "offset": offset, "limit": limit},
        )


    def renforge_parse_lint(text: str) -> dict:
        return _log_tool_call(
            name="renforge_parse_lint",
            params={"text": text},
            project_root=None,
            fn=parse_lint_text,
            args=(text,),
            kwargs={},
        )


    return {name: value for name, value in locals().items() if name in TOOL_NAMES}


def register(registrar, wrappers) -> None:
    registrar.register_many(wrappers, TOOL_NAMES)
