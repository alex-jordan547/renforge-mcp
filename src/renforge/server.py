"""MCP application bootstrap and compatibility fallback."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional


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


def _register_tools(app: Any) -> None:
    tool_decorator: Callable[..., Any] | None = getattr(app, "tool", None)
    if not callable(tool_decorator):
        return

    from .tools import live, project_ops
    from .tools.static import inspect_project, parse_lint_text, scan_project_index

    @tool_decorator()
    def renforge_info() -> str:
        return "RenForge MCP server is ready."

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
    def renforge_scan_project(project_path: str) -> dict:
        return _log_tool_call(
            name="renforge_scan_project",
            params={"project_path": project_path},
            project_root=project_path,
            fn=scan_project_index,
            args=(project_path,),
            kwargs={},
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
    def renforge_launch(project_path: str) -> dict:
        """Launch the Ren'Py project with the bridge, or reuse a running session."""
        return _log_tool_call(
            name="renforge_launch",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.launch_game,
            args=(project_path,),
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
        """Current label, on-screen tags and a snapshot of game variables."""
        return _log_tool_call(
            name="renforge_game_state",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.game_state,
            args=(project_path,),
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
    def renforge_screenshot(project_path: str):
        """Capture the current game frame (returned as an image the model can see)."""
        def _tool() -> Any:
            try:
                png = live.screenshot_png(project_path)
            except FileNotFoundError:
                return {"ok": False, "error": "no running game; call renforge_launch first"}
            except Exception as exc:  # pragma: no cover - defensive
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            # Return a raw MCP content block: helper classes like fastmcp.Image
            # moved between fastmcp versions, and an Image object from the
            # wrong package gets stringified instead of rendered.
            from mcp.types import ImageContent

            return ImageContent(
                type="image",
                data=base64.b64encode(png).decode("ascii"),
                mimeType="image/png",
            )

        return _log_tool_call(
            name="renforge_screenshot",
            params={"project_path": project_path},
            project_root=project_path,
            fn=_tool,
            args=(),
            kwargs={},
        )


def create_app() -> Any:
    backend_cls, _ = _get_fastmcp_backend()
    if backend_cls is None:
        return _FallbackServer()

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
