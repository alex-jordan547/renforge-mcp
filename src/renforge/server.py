"""MCP application bootstrap and compatibility fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


def _register_tools(app: Any) -> None:
    tool_decorator: Callable[..., Any] | None = getattr(app, "tool", None)
    if not callable(tool_decorator):
        return

    from .tools import live
    from .tools.static import inspect_project, parse_lint_text, scan_project_index

    @tool_decorator()
    def renforge_info() -> str:
        return "RenForge MCP server is ready."

    @tool_decorator()
    def renforge_inspect_project(project_path: str) -> dict:
        return inspect_project(project_path)

    @tool_decorator()
    def renforge_scan_project(project_path: str) -> dict:
        return scan_project_index(project_path)

    @tool_decorator()
    def renforge_parse_lint(text: str) -> dict:
        return parse_lint_text(text)

    # --- live game control (requires a display; under WSLg it works directly) ---

    @tool_decorator()
    def renforge_launch(project_path: str) -> dict:
        """Launch the Ren'Py project with the bridge, or reuse a running session."""
        return live.launch_game(project_path)

    @tool_decorator()
    def renforge_stop(project_path: str) -> dict:
        """Stop the running game and clean up the injected bridge."""
        return live.stop_game(project_path)

    @tool_decorator()
    def renforge_game_state(project_path: str) -> dict:
        """Current label, on-screen tags and a snapshot of game variables."""
        return live.game_state(project_path)

    @tool_decorator()
    def renforge_advance(project_path: str) -> dict:
        """Advance the current dialogue."""
        return live.advance(project_path)

    @tool_decorator()
    def renforge_list_choices(project_path: str) -> dict:
        """List the on-screen menu choices (text + index)."""
        return live.list_choices(project_path)

    @tool_decorator()
    def renforge_select_choice(project_path: str, text: str = "", index: int = -1) -> dict:
        """Select a menu choice by visible text (preferred) or by index."""
        return live.select_choice(project_path, text=text or None, index=index if index >= 0 else None)

    @tool_decorator()
    def renforge_eval(project_path: str, expr: str) -> dict:
        """Evaluate a Python expression in the running game's store namespace."""
        return live.eval_expr(project_path, expr)

    @tool_decorator()
    def renforge_get_var(project_path: str, name: str) -> dict:
        """Read a variable from the running game's store."""
        return live.get_var(project_path, name)

    @tool_decorator()
    def renforge_poll_events(project_path: str, since: int = 0) -> dict:
        """Return pushed events (dialogue, labels, exceptions) newer than `since`."""
        return live.poll_events(project_path, since)

    @tool_decorator()
    def renforge_screenshot(project_path: str):
        """Capture the current game frame (returned as an image the model can see)."""
        try:
            png = live.screenshot_png(project_path)
        except FileNotFoundError:
            return {"ok": False, "error": "no running game; call renforge_launch first"}
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        try:
            from fastmcp import Image  # type: ignore
        except Exception:
            from mcp.server.fastmcp import Image  # type: ignore
        return Image(data=png, format="png")


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
