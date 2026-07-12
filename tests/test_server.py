import asyncio
import base64
import io
import json

import pytest

from renforge.server import _FallbackServer, create_app

EXPECTED_TOOLS = {
    # static
    "renforge_info",
    "renforge_context",
    "renforge_inspect_project",
    "renforge_scan_project",
    "renforge_parse_lint",
    "renforge_inspect_image",
    "renforge_find_references",
    # live game control
    "renforge_launch",
    "renforge_jump",
    "renforge_new_game",
    "renforge_stop",
    "renforge_game_state",
    "renforge_game_state_compact",
    "renforge_advance",
    "renforge_control",
    "renforge_saves",
    "renforge_list_choices",
    "renforge_select_choice",
    "renforge_list_ui_elements",
    "renforge_click_element",
    "renforge_click_at",
    "renforge_eval",
    "renforge_get_var",
    "renforge_set_var",
    "renforge_poll_events",
    "renforge_screenshot",
    "renforge_find_image_on_screen",
    "renforge_get_displayable_bounds",
    "renforge_position_element",
    "renforge_diff_screenshots",
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
    instructions = getattr(app, "instructions", "") or ""
    assert "renforge_info" in instructions or not hasattr(app, "instructions")


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


def test_info_reports_the_project_selected_in_the_dashboard(tmp_path, monkeypatch) -> None:
    fastmcp = pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.session_registry import publish_dashboard

    monkeypatch.setenv("RENFORGE_RUNTIME_DIR", str(tmp_path / "runtime"))
    project = tmp_path / "game-project"
    publish_dashboard(project, url="http://127.0.0.1:8765/")

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool("renforge_info", {})

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert payload["active_project"] == str(project.resolve())
    assert payload["dashboard"]["url"] == "http://127.0.0.1:8765/"


def test_info_falls_back_to_the_server_default_project(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    monkeypatch.setenv("RENFORGE_RUNTIME_DIR", str(tmp_path / "empty-runtime"))
    app = create_app()
    app.project_root = tmp_path

    async def _call():
        async with Client(app) as client:
            return await client.call_tool("renforge_info", {})

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert payload["active_project"] == str(tmp_path.resolve())
    assert payload["dashboard"] is None


def test_scan_tool_accepts_bounded_queries(tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text(
        "label start:\n    jump ending\nlabel ending:\n    return\n",
        encoding="utf-8",
    )

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_scan_project",
                {
                    "project_path": str(tmp_path),
                    "sections": ["labels"],
                    "symbol": "ending",
                    "limit": 1,
                },
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert payload["labels"] == [{"file": "game/script.rpy", "line": 3, "name": "ending"}]
    assert "jumps" not in payload


def test_scan_tool_defaults_to_summary_only(tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_scan_project",
                {"project_path": str(tmp_path)},
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert set(payload) == {"summary", "pagination"}


def test_launch_tool_forwards_a_warp_target(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    calls = {}

    def fake_launch(project_path: str, version: str = "stable", warp: str | None = None):
        calls.update(project_path=project_path, version=version, warp=warp)
        return {"ok": True, "current_label": "chapter_two"}

    monkeypatch.setattr(live, "launch_game", fake_launch)

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_launch",
                {"project_path": str(tmp_path), "warp": "game/script.rpy:42"},
            )

    result = asyncio.run(_call())
    assert result.is_error is False
    assert calls == {
        "project_path": str(tmp_path),
        "version": "stable",
        "warp": "game/script.rpy:42",
    }


def test_launch_tool_prefers_the_active_dashboard_process(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge import dashboard_client
    from renforge.tools import live

    calls = {}

    def fake_dashboard_launch(project_path: str, *, version: str, warp: str | None):
        calls.update(project_path=project_path, version=version, warp=warp)
        return {"ok": True, "via": "dashboard"}

    monkeypatch.setattr(dashboard_client, "launch_game", fake_dashboard_launch)
    monkeypatch.setattr(
        live,
        "launch_game",
        lambda *_args, **_kwargs: pytest.fail("direct launch should not run"),
    )

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_launch",
                {"project_path": str(tmp_path), "warp": "game/script.rpy:42"},
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert payload == {"ok": True, "via": "dashboard"}
    assert calls == {
        "project_path": str(tmp_path),
        "version": "stable",
        "warp": "game/script.rpy:42",
    }


def test_jump_tool_resolves_a_label_and_relaunches_at_it(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text(
        "label start:\n    return\nlabel chapter_two:\n    return\n",
        encoding="utf-8",
    )
    calls = {}

    def fake_launch(project_path: str, version: str = "stable", warp: str | None = None):
        calls.update(project_path=project_path, version=version, warp=warp)
        return {"ok": True, "current_label": "chapter_two"}

    monkeypatch.setattr(live, "launch_game", fake_launch)

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_jump",
                {"project_path": str(tmp_path), "target": "chapter_two"},
            )

    result = asyncio.run(_call())
    assert result.is_error is False
    assert calls["project_path"] == str(tmp_path)
    assert calls["warp"].endswith("script.rpy:3")


def test_new_game_tool_relaunches_a_fresh_process_at_start(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    calls = {}

    def fake_launch(project_path: str, version: str = "stable", warp: str | None = None):
        calls.update(project_path=project_path, version=version, warp=warp)
        return {"ok": True, "current_label": "start"}

    monkeypatch.setattr(live, "launch_game", fake_launch)

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_new_game",
                {"project_path": str(tmp_path)},
            )

    result = asyncio.run(_call())
    assert result.is_error is False
    assert calls == {
        "project_path": str(tmp_path),
        "version": "stable",
        "warp": "game/script.rpy:1",
    }


def test_control_tool_dispatches_runtime_action_and_documents_valid_actions(
    tmp_path, monkeypatch
) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    calls = {}

    def fake_control(project_path: str, action: str):
        calls.update(project_path=project_path, action=action)
        return {"ok": True, "action": action}

    monkeypatch.setattr(live, "control", fake_control)
    app = create_app()

    async def _call():
        tools = await app.list_tools()
        async with Client(app) as client:
            result = await client.call_tool(
                "renforge_control",
                {"project_path": str(tmp_path), "action": "reload_script"},
            )
        return tools, result

    tools, result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    description = next(tool.description for tool in tools if tool.name == "renforge_control")
    valid_actions = {
        "advance",
        "rollback",
        "toggle_skip",
        "toggle_auto",
        "toggle_afm",
        "game_menu",
        "hide_windows",
        "quick_save",
        "quick_load",
        "reload_script",
        "restart_interaction",
        "quit",
    }

    assert payload == {"ok": True, "action": "reload_script"}
    assert calls == {"project_path": str(tmp_path), "action": "reload_script"}
    assert all(action in description for action in valid_actions)


def test_saves_tool_dispatches_grouped_save_action(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    calls = {}

    def fake_saves(project_path, action, slot=None, extra_info=None, regexp=None):
        calls.update(
            project_path=project_path,
            action=action,
            slot=slot,
            extra_info=extra_info,
            regexp=regexp,
        )
        return {"ok": True, "slot": slot, "extra_info": extra_info}

    monkeypatch.setattr(live, "saves", fake_saves)

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_saves",
                {
                    "project_path": str(tmp_path),
                    "action": "save",
                    "slot": "branch-a",
                    "extra_info": "before menu",
                },
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))

    assert payload == {"ok": True, "slot": "branch-a", "extra_info": "before menu"}
    assert calls == {
        "project_path": str(tmp_path),
        "action": "save",
        "slot": "branch-a",
        "extra_info": "before menu",
        "regexp": None,
    }


def test_saves_tool_validates_action_and_required_slot(tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client
    from renforge.tools import live

    async def _call(action, slot=None):
        async with Client(create_app()) as client:
            payload = {"project_path": str(tmp_path), "action": action}
            if slot is not None:
                payload["slot"] = slot
            result = await client.call_tool("renforge_saves", payload)
        return json.loads(next(block.text for block in result.content if block.type == "text"))

    invalid_action = asyncio.run(_call("archive"))
    missing_slot = asyncio.run(_call("save"))

    assert invalid_action == {
        "ok": False,
        "error": "action must be one of: save, load, list",
    }
    assert missing_slot == {
        "ok": False,
        "error": "slot is required for action 'save'",
    }
    assert live.saves(str(tmp_path), "list", regexp=123) == {
        "ok": False,
        "error": "regexp must be a string",
    }


def test_saves_tool_dispatches_load_and_list_actions(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    calls = []

    def fake_saves(project_path, action, slot=None, extra_info=None, regexp=None):
        calls.append(
            {
                "project_path": project_path,
                "action": action,
                "slot": slot,
                "extra_info": extra_info,
                "regexp": regexp,
            }
        )
        return {"ok": True, "action": action}

    monkeypatch.setattr(live, "saves", fake_saves)

    async def _call(payload):
        async with Client(create_app()) as client:
            return await client.call_tool("renforge_saves", payload)

    load = asyncio.run(
        _call({"project_path": str(tmp_path), "action": "load", "slot": "branch-a"})
    )
    listed = asyncio.run(
        _call({"project_path": str(tmp_path), "action": "list", "regexp": "branch"})
    )

    assert json.loads(next(block.text for block in load.content if block.type == "text")) == {
        "ok": True,
        "action": "load",
    }
    assert json.loads(next(block.text for block in listed.content if block.type == "text")) == {
        "ok": True,
        "action": "list",
    }
    assert calls == [
        {
            "project_path": str(tmp_path),
            "action": "load",
            "slot": "branch-a",
            "extra_info": None,
            "regexp": None,
        },
        {
            "project_path": str(tmp_path),
            "action": "list",
            "slot": None,
            "extra_info": None,
            "regexp": "branch",
        },
    ]


def test_saves_tool_is_listed_with_grouped_actions() -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")

    tools = asyncio.run(create_app().list_tools())
    tool = next(tool for tool in tools if tool.name == "renforge_saves")

    assert all(action in tool.description for action in ("save", "load", "list"))
    assert tool.parameters["required"] == ["project_path", "action"]
    assert set(tool.parameters["properties"]) == {
        "project_path",
        "action",
        "slot",
        "extra_info",
        "regexp",
    }


def test_ui_tools_expose_semantic_elements_and_coordinate_guards(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    calls = {}
    monkeypatch.setattr(
        live,
        "list_ui_elements",
        lambda path, **kwargs: {
            "ok": True,
            "frame_id": "frame-1",
            "elements": [
                {
                    "id": "save-1",
                    "text": "Save",
                    "type": "button",
                    "bounds": {"x": 20, "y": 30, "width": 80, "height": 40},
                }
            ],
            "screen": "quick_menu",
        },
    )

    def fake_click_element(path, **kwargs):
        calls["element"] = (path, kwargs)
        return {"ok": True, "id": kwargs["element_id"], "x": 60, "y": 50}

    def fake_click_at(path, x, y, **kwargs):
        calls["at"] = (path, x, y, kwargs)
        return {"ok": True, "x": x, "y": y}

    monkeypatch.setattr(live, "click_element", fake_click_element)
    monkeypatch.setattr(live, "click_at", fake_click_at)

    async def _call():
        async with Client(create_app()) as client:
            listed = await client.call_tool(
                "renforge_list_ui_elements", {"project_path": str(tmp_path)}
            )
            clicked = await client.call_tool(
                "renforge_click_element",
                {
                    "project_path": str(tmp_path),
                    "element_id": "save-1",
                    "expected_frame_id": "frame-1",
                },
            )
            by_coord = await client.call_tool(
                "renforge_click_at",
                {
                    "project_path": str(tmp_path),
                    "x": 60,
                    "y": 50,
                    "expected_frame_id": "frame-1",
                    "expected_state": {"menu": True},
                    "coordinate_space": "screenshot",
                },
            )
            return listed, clicked, by_coord

    listed, clicked, by_coord = asyncio.run(_call())
    listed_payload = json.loads(next(block.text for block in listed.content if block.type == "text"))
    clicked_payload = json.loads(next(block.text for block in clicked.content if block.type == "text"))
    coord_payload = json.loads(next(block.text for block in by_coord.content if block.type == "text"))
    assert listed_payload["elements"][0]["bounds"]["width"] == 80
    assert clicked_payload == {"ok": True, "id": "save-1", "x": 60, "y": 50}
    assert coord_payload == {"ok": True, "x": 60, "y": 50}
    assert calls["element"][1]["expected_frame_id"] == "frame-1"
    assert calls["at"][3]["expected_state"] == {"menu": True}
    assert calls["at"][3]["coordinate_space"] == "screenshot"


def test_find_image_on_screen_returns_template_bounds(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from fastmcp import Client

    from renforge.tools import live

    game = tmp_path / "game"
    game.mkdir()
    source = image_module.new("RGB", (30, 20), "black")
    source.paste("white", (12, 6, 16, 10))
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    template = game / "save.png"
    source.crop((12, 6, 16, 10)).save(template)
    monkeypatch.setattr(live, "screenshot_png", lambda _path: encoded.getvalue())

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_find_image_on_screen",
                {"project_path": str(tmp_path), "template_path": "game/save.png"},
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert payload["ok"] is True
    assert payload["matches"][0]["bounds"] == {
        "x": 12,
        "y": 6,
        "width": 4,
        "height": 4,
    }
    assert len(payload["frame_id"]) == 64
    assert payload["coordinate_space"] == "screenshot"
    assert payload["click_hint"]["expected_frame_id"] == payload["frame_id"]


def test_inspect_image_crops_and_zooms_without_external_scripts(tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from fastmcp import Client

    source = tmp_path / "mockup.png"
    image = image_module.new("RGB", (100, 60), "red")
    image.paste("blue", (50, 0, 100, 60))
    image.save(source)

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_inspect_image",
                {
                    "image_path": str(source),
                    "crop_x": 50,
                    "crop_y": 0,
                    "crop_width": 50,
                    "crop_height": 60,
                    "scale": 2.0,
                },
            )

    result = asyncio.run(_call())
    block = next(block for block in result.content if block.type == "image")
    cropped = image_module.open(io.BytesIO(base64.b64decode(block.data)))
    assert cropped.size == (100, 120)
    assert cropped.getpixel((50, 60))[:3] == (0, 0, 255)


def test_screenshot_can_crop_and_zoom_the_live_frame(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from fastmcp import Client

    from renforge.tools import live

    source = image_module.new("RGB", (100, 60), "red")
    source.paste("blue", (50, 0, 100, 60))
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")
    calls = {}

    def fake_screenshot(path: str, width: int = 0, height: int = 0) -> bytes:
        calls.update(path=path, width=width, height=height)
        return encoded.getvalue()

    monkeypatch.setattr(live, "screenshot_png", fake_screenshot)

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_screenshot",
                {
                    "project_path": str(tmp_path),
                    "width": 100,
                    "height": 60,
                    "crop_x": 50,
                    "crop_y": 0,
                    "crop_width": 50,
                    "crop_height": 60,
                    "scale": 2.0,
                },
            )

    result = asyncio.run(_call())
    block = next(block for block in result.content if block.type == "image")
    cropped = image_module.open(io.BytesIO(base64.b64decode(block.data)))
    assert cropped.size == (100, 120)
    assert calls == {"path": str(tmp_path), "width": 100, "height": 60}


def test_find_references_tool_reports_unused_definitions(tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    game = tmp_path / "game"
    game.mkdir()
    (game / "ui.rpy").write_text(
        'define DEAD_ICON = "images/dead.png"\n# DEAD_ICON\n',
        encoding="utf-8",
    )

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_find_references",
                {"project_path": str(tmp_path), "symbol": "DEAD_ICON"},
            )

    result = asyncio.run(_call())
    payload = json.loads(next(block.text for block in result.content if block.type == "text"))
    assert payload["unused"] is True
    assert payload["definition_count"] == 1
    assert payload["reference_count"] == 0


def test_game_state_preserves_full_state_and_compact_tool_can_select_variables(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    monkeypatch.setattr(
        live,
        "game_state",
        lambda _path: {
            "ok": True,
            "current_label": "start",
            "showing_tags": ["bg"],
            "menu": False,
            "variables": {"score": 7, "player_name": "Rin", "ICON_UNUSED": "x.png"},
        },
    )

    async def _call():
        async with Client(create_app()) as client:
            full = await client.call_tool(
                "renforge_game_state",
                {"project_path": str(tmp_path)},
            )
            selected = await client.call_tool(
                "renforge_game_state_compact",
                {"project_path": str(tmp_path), "variable_names": ["score"]},
            )
            return full, selected

    full_result, selected_result = asyncio.run(_call())
    full = json.loads(next(block.text for block in full_result.content if block.type == "text"))
    selected = json.loads(next(block.text for block in selected_result.content if block.type == "text"))
    assert full["variables"] == {
        "score": 7,
        "player_name": "Rin",
        "ICON_UNUSED": "x.png",
    }
    assert selected["variable_count"] == 3
    assert selected["variables"] == {"score": 7}


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


def test_placement_tools_forward_to_the_bridge_client(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client

    from renforge.tools import live

    calls = {}

    def fake_bounds(path, tag, **kwargs):
        calls["bounds"] = (path, tag, kwargs)
        return {"ok": True, "tag": tag, "bounds": {"x": 400, "y": 300, "width": 200, "height": 400}}

    def fake_position(path, tag, **kwargs):
        calls["position"] = (path, tag, kwargs)
        return {"ok": True, "tag": tag, "bounds": {"x": 960, "y": 100, "width": 200, "height": 400}}

    monkeypatch.setattr(live, "get_displayable_bounds", fake_bounds)
    monkeypatch.setattr(live, "position_element", fake_position)

    async def _call():
        async with Client(create_app()) as client:
            bounds = await client.call_tool(
                "renforge_get_displayable_bounds",
                {"project_path": str(tmp_path), "tag": "eileen"},
            )
            moved = await client.call_tool(
                "renforge_position_element",
                {"project_path": str(tmp_path), "tag": "eileen", "xpos": 960, "xanchor": 0.5},
            )
            return bounds, moved

    bounds, moved = asyncio.run(_call())
    bounds_payload = json.loads(next(b.text for b in bounds.content if b.type == "text"))
    moved_payload = json.loads(next(b.text for b in moved.content if b.type == "text"))
    assert bounds_payload["bounds"]["x"] == 400
    assert moved_payload["bounds"]["x"] == 960
    assert calls["bounds"][1] == "eileen"
    assert calls["position"][2] == {"layer": None, "xpos": 960, "xanchor": 0.5}


def test_diff_screenshots_tool_reports_the_changed_region(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from fastmcp import Client

    from renforge.tools import live

    before = image_module.new("RGB", (120, 80), "black")
    before.save(tmp_path / "before.png")
    after = image_module.new("RGB", (120, 80), "black")
    after.paste("white", (20, 10, 40, 30))
    encoded = io.BytesIO()
    after.save(encoded, format="PNG")
    monkeypatch.setattr(live, "screenshot_png", lambda _path: encoded.getvalue())

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_diff_screenshots",
                {"project_path": str(tmp_path), "before_path": "before.png"},
            )

    result = asyncio.run(_call())
    payload = json.loads(next(b.text for b in result.content if b.type == "text"))
    assert payload["changed"] is True
    assert payload["bounds"] == {"x": 20, "y": 10, "width": 20, "height": 20}


def test_screenshot_can_overlay_a_measurement_grid(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    import base64

    from fastmcp import Client

    from renforge.tools import live

    frame = image_module.new("RGB", (200, 120), "navy")
    encoded = io.BytesIO()
    frame.save(encoded, format="PNG")
    monkeypatch.setattr(live, "screenshot_png", lambda *a, **k: encoded.getvalue())

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_screenshot",
                {"project_path": str(tmp_path), "grid": 50, "rulers": True},
            )

    result = asyncio.run(_call())
    image_blocks = [b for b in result.content if getattr(b, "type", None) == "image"]
    assert image_blocks, f"expected an image block, got: {result.content!r}"
    annotated = image_module.open(io.BytesIO(base64.b64decode(image_blocks[0].data)))
    assert annotated.size == (200, 120)
    assert annotated.convert("RGB").getpixel((50, 60)) != (0, 0, 128)
