import asyncio

import pytest

from renforge.server import _FallbackServer, create_app

EXPECTED_TOOLS = {
    # static
    "renforge_info",
    "renforge_inspect_project",
    "renforge_scan_project",
    "renforge_parse_lint",
    # live game control
    "renforge_launch",
    "renforge_stop",
    "renforge_game_state",
    "renforge_advance",
    "renforge_list_choices",
    "renforge_select_choice",
    "renforge_eval",
    "renforge_get_var",
    "renforge_set_var",
    "renforge_poll_events",
    "renforge_screenshot",
    "renforge_autopilot",
    # assets / translation / build / docs
    "renforge_assets",
    "renforge_languages",
    "renforge_translation_stats",
    "renforge_generate_translations",
    "renforge_export_dialogue",
    "renforge_web_build",
    "renforge_distribute",
    "renforge_search_docs",
    "renforge_get_doc",
    "renforge_list_docs",
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


def test_live_tools_error_cleanly_without_a_running_game(tmp_path) -> None:
    from renforge.tools import live

    # A valid project directory but no running bridge.
    (tmp_path / "game").mkdir()
    result = live.game_state(str(tmp_path))
    assert result["ok"] is False
    assert "error" in result

    # An invalid project path is reported, not raised.
    launched = live.launch_game(str(tmp_path / "does-not-exist"))
    assert launched["ok"] is False


def test_screenshot_serializes_to_an_mcp_image_block(tmp_path, monkeypatch) -> None:
    fastmcp = pytest.importorskip("fastmcp", reason="fastmcp not installed")
    import base64

    from fastmcp import Client

    from renforge.tools import live

    monkeypatch.setattr(live, "screenshot_png", lambda path: b"fake-png-bytes")
    app = create_app()
    if isinstance(app, _FallbackServer):
        pytest.skip("MCP backend (mcp/fastmcp) not installed")

    async def _call():
        async with Client(app) as client:
            return await client.call_tool(
                "renforge_screenshot", {"project_path": str(tmp_path)}
            )

    result = asyncio.run(_call())
    image_blocks = [block for block in result.content if getattr(block, "type", None) == "image"]
    assert image_blocks, f"expected an image content block, got: {result.content!r}"
    assert image_blocks[0].mimeType == "image/png"
    assert base64.b64decode(image_blocks[0].data) == b"fake-png-bytes"
