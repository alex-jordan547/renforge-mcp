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
