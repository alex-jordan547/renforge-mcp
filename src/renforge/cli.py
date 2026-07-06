"""CLI entrypoints for the RenForge package."""

from __future__ import annotations

import argparse
import json
from typing import Any

from . import __version__
from .tools.static import inspect_project
from . import server


def _run_inspect(path_arg: str) -> int:
    payload: dict[str, Any] = inspect_project(path_arg)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_serve(project: str | None) -> int:
    return server.run_server(project_root=project)


def _run_ui(project: str, host: str, port: int, open_browser: bool) -> int:
    from .ui.server import run_ui_server

    return run_ui_server(project, host=host, port=port, open_browser=open_browser)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="renforge", description="RenForge CLI")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command")

    serve = subcommands.add_parser("serve", help="Start the MCP server")
    serve.add_argument(
        "--project",
        "-p",
        default=None,
        help="Project root path (defaults to current directory)",
    )

    inspect_cmd = subcommands.add_parser("inspect", help="Inspect a Ren'Py project")
    inspect_cmd.add_argument(
        "project",
        default=".",
        nargs="?",
        help="Project root path",
    )

    ui_cmd = subcommands.add_parser("ui", help="Start RenForge dashboard")
    ui_cmd.add_argument(
        "--project",
        "-p",
        required=False,
        default=".",
        help="Project root path",
    )
    ui_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    ui_cmd.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Listen port (default: 8765)",
    )
    ui_cmd.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the dashboard in the browser",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        return _run_serve(args.project)

    if args.command == "inspect":
        return _run_inspect(args.project)

    if args.command == "ui":
        return _run_ui(args.project, args.host, args.port, not args.no_open)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
