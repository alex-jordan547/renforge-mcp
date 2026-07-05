import asyncio

import pytest

from renforge.server import _FallbackServer, create_app

EXPECTED_TOOLS = {
    "renforge_info",
    "renforge_inspect_project",
    "renforge_scan_project",
    "renforge_parse_lint",
}


def test_fallback_server_runs_cleanly() -> None:
    assert _FallbackServer().run() == 0


def test_create_app_registers_expected_tools() -> None:
    app = create_app()
    if isinstance(app, _FallbackServer):
        pytest.skip("MCP backend (mcp/fastmcp) not installed")

    tools = asyncio.run(app.list_tools())
    names = {tool.name for tool in tools}
    assert EXPECTED_TOOLS <= names
