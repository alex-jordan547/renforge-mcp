"""MCP application bootstrap and compatibility fallback."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional

from . import __version__, session_registry


@dataclass
class _FallbackServer:
    project_root: Path | None = None

    def run(self, *_, **__) -> int:
        print(
            "RenForge fallback mode: FastMCP backend unavailable. "
            "Install 'fastmcp' or 'mcp>=1.0.0' to enable MCP transport."
        )
        if self.project_root:
            print(f"Target project: {self.project_root}")
        return 0


def _get_fastmcp_backend() -> tuple[Optional[type], Optional[str]]:
    try:
        from fastmcp import FastMCP  # type: ignore

        return FastMCP, "fastmcp"
    except Exception:
        pass

    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore

        return FastMCP, "mcp"
    except Exception:
        return None, None


def _log_tool_call(
    *,
    name: str,
    params: dict[str, Any],
    project_root: str | None,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    from . import activity_log

    started = perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    duration_ms = round((perf_counter() - started) * 1000, 2)
    if project_root is not None:
        summary = activity_log.summarize_result(result)
        try:
            activity_log.log_tool_call(
                project_root,
                name,
                params,
                duration_ms,
                result,
                files_touched=summary["files_touched"],
            )
        except Exception:
            pass

    return result


def _png_content(png: bytes) -> Any:
    from mcp.types import ImageContent

    return ImageContent(
        type="image",
        data=base64.b64encode(png).decode("ascii"),
        mimeType="image/png",
    )


def _register_tools(app: Any) -> None:
    tool_decorator: Callable[..., Any] | None = getattr(app, "tool", None)
    if not callable(tool_decorator):
        return

    from .tools import live, project_ops
    from .tools.static import inspect_project, parse_lint_text, scan_project_index

    def _launch_game(
        project_path: str,
        *,
        version: str = "stable",
        warp: str | None = None,
    ) -> dict:
        from .dashboard_client import launch_game as launch_via_dashboard

        delegated = launch_via_dashboard(project_path, version=version, warp=warp)
        if delegated is not None:
            return delegated
        return live.launch_game(project_path, version=version, warp=warp)

    def _context_payload() -> dict[str, Any]:
        dashboard = session_registry.active_dashboard()
        default_project = getattr(app, "project_root", None)
        active_project = dashboard.get("project") if dashboard else None
        if active_project is None and default_project is not None:
            active_project = str(Path(default_project).expanduser().resolve())
        return {
            "ok": True,
            "version": __version__,
            "active_project": active_project,
            "dashboard": dashboard,
        }

    @tool_decorator()
    def renforge_info() -> dict:
        """Call first: report RenForge version and the project selected in the dashboard."""
        return _context_payload()

    @tool_decorator()
    def renforge_context() -> dict:
        """Discover the active dashboard and its currently selected Ren'Py project."""
        return _context_payload()

    @tool_decorator()
    def renforge_inspect_image(
        image_path: str,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_width: int = 0,
        crop_height: int = 0,
        scale: float = 1.0,
    ):
        """Open a local image and return an optional cropped/zoomed PNG for inspection."""
        from .image_ops import inspect_image

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

    @tool_decorator()
    def renforge_inspect_project(project_path: str) -> dict:
        return _log_tool_call(
            name="renforge_inspect_project",
            params={"project_path": project_path},
            project_root=project_path,
            fn=inspect_project,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
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

    @tool_decorator()
    def renforge_find_references(
        project_path: str,
        symbol: str,
        file_glob: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> dict:
        """Find exact Ren'Py definitions/usages, including text interpolations."""
        from .symbols import find_references

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

    @tool_decorator()
    def renforge_parse_lint(text: str) -> dict:
        return _log_tool_call(
            name="renforge_parse_lint",
            params={"text": text},
            project_root=None,
            fn=parse_lint_text,
            args=(text,),
            kwargs={},
        )

    # --- live game control (requires a display; under WSLg it works directly) ---

    @tool_decorator()
    def renforge_launch(project_path: str, warp: str = "", version: str = "stable") -> dict:
        """Launch or reuse a game; set warp to a Ren'Py file:line target."""
        return _log_tool_call(
            name="renforge_launch",
            params={"project_path": project_path, "warp": warp, "version": version},
            project_root=project_path,
            fn=_launch_game,
            args=(project_path,),
            kwargs={"version": version, "warp": warp or None},
        )

    @tool_decorator()
    def renforge_jump(project_path: str, target: str, version: str = "stable") -> dict:
        """Restart the game at a label or file:line target using Ren'Py warp."""
        from .navigation import resolve_warp_target

        def _jump() -> dict:
            resolved = resolve_warp_target(project_path, target)
            if not resolved.get("ok"):
                return resolved
            return _launch_game(
                project_path,
                version=version,
                warp=str(resolved["target"]),
            )

        return _log_tool_call(
            name="renforge_jump",
            params={"project_path": project_path, "target": target, "version": version},
            project_root=project_path,
            fn=_jump,
            args=(),
            kwargs={},
        )

    @tool_decorator()
    def renforge_new_game(project_path: str, version: str = "stable") -> dict:
        """Start a fresh process at the project's ``start`` label."""
        from .navigation import resolve_warp_target

        def _new_game() -> dict:
            resolved = resolve_warp_target(project_path, "start")
            if not resolved.get("ok"):
                return resolved
            return _launch_game(
                project_path,
                version=version,
                warp=str(resolved["target"]),
            )

        return _log_tool_call(
            name="renforge_new_game",
            params={"project_path": project_path, "version": version},
            project_root=project_path,
            fn=_new_game,
            args=(),
            kwargs={},
        )

    @tool_decorator()
    def renforge_stop(project_path: str) -> dict:
        """Stop the running game and clean up the injected bridge."""
        return _log_tool_call(
            name="renforge_stop",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.stop_game,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_game_state(project_path: str) -> dict:
        """Return the complete live state, including variables (backward compatible)."""
        return _log_tool_call(
            name="renforge_game_state",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.game_state,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_game_state_compact(
        project_path: str,
        variable_names: list[str] | None = None,
        variable_prefix: str = "",
    ) -> dict:
        """Return bounded live state, optionally with selected variables."""
        def _state() -> dict:
            state = live.game_state(project_path)
            if not state.get("ok"):
                return state
            result = dict(state)
            variables = result.pop("variables", {})
            if not isinstance(variables, dict):
                variables = {}
            result["variable_count"] = len(variables)
            requested = set(variable_names or [])
            if requested or variable_prefix:
                result["variables"] = {
                    name: value
                    for name, value in variables.items()
                    if name in requested or (variable_prefix and name.startswith(variable_prefix))
                }
            return result

        return _log_tool_call(
            name="renforge_game_state_compact",
            params={
                "project_path": project_path,
                "variable_names": variable_names,
                "variable_prefix": variable_prefix,
            },
            project_root=project_path,
            fn=_state,
            args=(),
            kwargs={},
        )

    @tool_decorator()
    def renforge_advance(project_path: str) -> dict:
        """Advance the current dialogue."""
        return _log_tool_call(
            name="renforge_advance",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.advance,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_list_choices(project_path: str) -> dict:
        """List the on-screen menu choices (text + index)."""
        return _log_tool_call(
            name="renforge_list_choices",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.list_choices,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_select_choice(project_path: str, text: str = "", index: int = -1) -> dict:
        """Select a menu choice by visible text (preferred) or by index."""
        return _log_tool_call(
            name="renforge_select_choice",
            params={"project_path": project_path, "text": text, "index": index},
            project_root=project_path,
            fn=live.select_choice,
            args=(project_path,),
            kwargs={"text": text or None, "index": index if index >= 0 else None},
        )

    @tool_decorator()
    def renforge_list_ui_elements(
        project_path: str,
        screen: str = "",
        text: str = "",
        element_type: str = "",
    ) -> dict:
        """List visible focusable Ren'Py controls with bounds and frame guard."""
        return _log_tool_call(
            name="renforge_list_ui_elements",
            params={
                "project_path": project_path,
                "screen": screen,
                "text": text,
                "element_type": element_type,
            },
            project_root=project_path,
            fn=live.list_ui_elements,
            args=(project_path,),
            kwargs={
                "screen": screen or None,
                "text": text or None,
                "element_type": element_type or None,
            },
        )

    @tool_decorator()
    def renforge_click_element(
        project_path: str,
        text: str = "",
        element_id: str = "",
        screen: str = "",
        exact: bool = False,
        expected_frame_id: str = "",
    ) -> dict:
        """Click a visible control by text/id, guarded against a stale frame."""
        return _log_tool_call(
            name="renforge_click_element",
            params={
                "project_path": project_path,
                "text": text,
                "element_id": element_id,
                "screen": screen,
                "exact": exact,
                "expected_frame_id": expected_frame_id,
            },
            project_root=project_path,
            fn=live.click_element,
            args=(project_path,),
            kwargs={
                "text": text or None,
                "element_id": element_id or None,
                "screen": screen or None,
                "exact": exact,
                "expected_frame_id": expected_frame_id or None,
            },
        )

    @tool_decorator()
    def renforge_click_at(
        project_path: str,
        x: float,
        y: float,
        expected_frame_id: str = "",
        expected_state: dict[str, Any] | None = None,
        coordinate_space: str = "logical",
    ) -> dict:
        """Click screen coordinates with optional frame/state safety guards."""
        return _log_tool_call(
            name="renforge_click_at",
            params={
                "project_path": project_path,
                "x": x,
                "y": y,
                "expected_frame_id": expected_frame_id,
                "expected_state": expected_state,
                "coordinate_space": coordinate_space,
            },
            project_root=project_path,
            fn=live.click_at,
            args=(project_path, x, y),
            kwargs={
                "expected_frame_id": expected_frame_id or None,
                "expected_state": expected_state,
                "coordinate_space": coordinate_space,
            },
        )

    @tool_decorator()
    def renforge_eval(project_path: str, expr: str) -> dict:
        """Evaluate a Python expression in the running game's store namespace."""
        return _log_tool_call(
            name="renforge_eval",
            params={"project_path": project_path, "expr": expr},
            project_root=project_path,
            fn=live.eval_expr,
            args=(project_path, expr),
            kwargs={},
        )

    @tool_decorator()
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

    @tool_decorator()
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

    @tool_decorator()
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

    @tool_decorator()
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

    # --- assets / translation / build / docs (SDK-backed, static) ---

    @tool_decorator()
    def renforge_assets(project_path: str) -> dict:
        """Find orphaned and missing image/audio assets in the project."""
        return _log_tool_call(
            name="renforge_assets",
            params={"project_path": project_path},
            project_root=project_path,
            fn=project_ops.assets,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_languages(project_path: str) -> dict:
        """List translation languages present under game/tl/."""
        return _log_tool_call(
            name="renforge_languages",
            params={"project_path": project_path},
            project_root=project_path,
            fn=project_ops.languages,
            args=(project_path,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_translation_stats(project_path: str, language: str) -> dict:
        """Report missing dialogue/string translation counts for a language."""
        return _log_tool_call(
            name="renforge_translation_stats",
            params={"project_path": project_path, "language": language},
            project_root=project_path,
            fn=project_ops.translation_stats,
            args=(project_path, language),
            kwargs={},
        )

    @tool_decorator()
    def renforge_generate_translations(project_path: str, language: str) -> dict:
        """Generate/update translation files for a language (writes game/tl/<language>/)."""
        return _log_tool_call(
            name="renforge_generate_translations",
            params={"project_path": project_path, "language": language},
            project_root=project_path,
            fn=project_ops.generate_translations,
            args=(project_path, language),
            kwargs={},
        )

    @tool_decorator()
    def renforge_export_dialogue(project_path: str, language: str = "None") -> dict:
        """Export the game's dialogue as plain text."""
        return _log_tool_call(
            name="renforge_export_dialogue",
            params={"project_path": project_path, "language": language},
            project_root=project_path,
            fn=project_ops.export_dialogue,
            args=(project_path, language),
            kwargs={},
        )

    @tool_decorator()
    def renforge_web_build(project_path: str, destination: str = "") -> dict:
        """Package the project as a browser-playable build (needs the web DLC)."""
        return _log_tool_call(
            name="renforge_web_build",
            params={"project_path": project_path, "destination": destination},
            project_root=project_path,
            fn=project_ops.web_build,
            args=(project_path,),
            kwargs={"destination": destination},
        )

    @tool_decorator()
    def renforge_distribute(project_path: str, package: str = "", destination: str = "") -> dict:
        """Build desktop distributions (e.g. package='pc', 'mac', 'linux')."""
        return _log_tool_call(
            name="renforge_distribute",
            params={"project_path": project_path, "package": package, "destination": destination},
            project_root=project_path,
            fn=project_ops.distribute,
            args=(project_path,),
            kwargs={"package": package, "destination": destination},
        )

    @tool_decorator()
    def renforge_search_docs(query: str) -> dict:
        """Search Ren'Py's offline documentation for a keyword."""
        return _log_tool_call(
            name="renforge_search_docs",
            params={"query": query},
            project_root=None,
            fn=project_ops.search_docs,
            args=(query,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_get_doc(topic: str) -> dict:
        """Read a Ren'Py documentation page as plain text (e.g. topic='cli')."""
        return _log_tool_call(
            name="renforge_get_doc",
            params={"topic": topic},
            project_root=None,
            fn=project_ops.get_doc,
            args=(topic,),
            kwargs={},
        )

    @tool_decorator()
    def renforge_list_docs() -> dict:
        """List available Ren'Py documentation topics."""
        return _log_tool_call(
            name="renforge_list_docs",
            params={},
            project_root=None,
            fn=project_ops.list_docs,
            args=(),
            kwargs={},
        )

    @tool_decorator()
    def renforge_screenshot(
        project_path: str,
        width: int = 0,
        height: int = 0,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_width: int = 0,
        crop_height: int = 0,
        scale: float = 1.0,
    ):
        """Capture a game frame, optionally resizing, cropping, and zooming it."""
        def _tool() -> Any:
            try:
                if (width == 0) != (height == 0):
                    raise ValueError("width and height must be provided together")
                if width or height:
                    png = live.screenshot_png(project_path, width=width, height=height)
                else:
                    png = live.screenshot_png(project_path)
                if crop_width or crop_height or crop_x or crop_y or scale != 1.0:
                    from .image_ops import transform_png

                    png = transform_png(
                        png,
                        crop_x=crop_x,
                        crop_y=crop_y,
                        crop_width=crop_width,
                        crop_height=crop_height,
                        scale=scale,
                    )
            except FileNotFoundError:
                return {"ok": False, "error": "no running game; call renforge_launch first"}
            except Exception as exc:  # pragma: no cover - defensive
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            # Return a raw MCP content block: helper classes like fastmcp.Image
            # moved between fastmcp versions, and an Image object from the
            # wrong package gets stringified instead of rendered.
            return _png_content(png)

        return _log_tool_call(
            name="renforge_screenshot",
            params={
                "project_path": project_path,
                "width": width,
                "height": height,
                "crop_x": crop_x,
                "crop_y": crop_y,
                "crop_width": crop_width,
                "crop_height": crop_height,
                "scale": scale,
            },
            project_root=project_path,
            fn=_tool,
            args=(),
            kwargs={},
        )

    @tool_decorator()
    def renforge_find_image_on_screen(
        project_path: str,
        template_path: str,
        threshold: float = 0.92,
        max_matches: int = 20,
        region_x: int = 0,
        region_y: int = 0,
        region_width: int = 0,
        region_height: int = 0,
    ) -> dict:
        """Find a template image in the current frame and return its bounds."""
        def _find() -> dict:
            from .image_ops import find_image_matches

            if (region_width == 0) != (region_height == 0):
                raise ValueError("region_width and region_height must be provided together")
            if (region_x or region_y) and not (region_width and region_height):
                raise ValueError("region coordinates require region_width and region_height")
            screenshot = live.screenshot_png(project_path)
            template = Path(template_path).expanduser()
            if not template.is_absolute():
                template = Path(project_path).expanduser() / template
            region = (
                (region_x, region_y, region_width, region_height)
                if region_width and region_height
                else None
            )
            result = find_image_matches(
                screenshot,
                template,
                threshold=threshold,
                max_matches=max_matches,
                region=region,
            )
            result["frame_id"] = hashlib.sha256(screenshot).hexdigest()
            result["coordinate_space"] = "screenshot"
            result["click_hint"] = {
                "coordinate_space": "screenshot",
                "expected_frame_id": result["frame_id"],
            }
            return result

        return _log_tool_call(
            name="renforge_find_image_on_screen",
            params={
                "project_path": project_path,
                "template_path": template_path,
                "threshold": threshold,
                "max_matches": max_matches,
                "region_x": region_x,
                "region_y": region_y,
                "region_width": region_width,
                "region_height": region_height,
            },
            project_root=project_path,
            fn=_find,
            args=(),
            kwargs={},
        )


def create_app() -> Any:
    backend_cls, _ = _get_fastmcp_backend()
    if backend_cls is None:
        return _FallbackServer()

    instructions = (
        "Call renforge_info first. It reports the project selected in the RenForge "
        "dashboard; use active_project for project_path instead of guessing. "
        "When that dashboard is active, renforge_launch delegates display-bound startup "
        "to its process automatically. Prefer bounded scan queries and "
        "renforge_game_state_compact for large results. For UI interaction, call "
        "renforge_list_ui_elements first, then pass its frame_id to "
        "renforge_click_element or renforge_click_at; use "
        "renforge_find_image_on_screen for visual template placement."
    )
    try:
        app = backend_cls("renforge", instructions=instructions)
    except TypeError:  # pragma: no cover - compatibility with older MCP backends
        app = backend_cls("renforge")
    _register_tools(app)
    return app


def run_server(project_root: str | None = None, transport: str = "stdio") -> int:
    app = create_app()
    normalized = Path(project_root).expanduser().resolve() if project_root else None
    if isinstance(app, _FallbackServer):
        if normalized is not None:
            app.project_root = normalized
        return app.run()

    if normalized is not None:
        app.project_root = normalized  # type: ignore[attr-defined]

    runner = getattr(app, "run", None)
    if not callable(runner):
        return 0

    try:
        result = runner(transport=transport)
    except TypeError:
        try:
            result = runner()
        except Exception as exc:
            raise RuntimeError(f"Failed to run MCP server: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to run MCP server: {exc}") from exc

    return 0 if result is None else int(result)
