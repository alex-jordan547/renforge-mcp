import asyncio
import json

import pytest

from renforge.server import _FallbackServer, create_app
from renforge.tool_definitions import TOOL_DEFINITIONS


def _annotations_to_dict(raw: object) -> dict[str, bool]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return {
        "readOnlyHint": getattr(raw, "readOnlyHint", None),
        "idempotentHint": getattr(raw, "idempotentHint", None),
        "destructiveHint": getattr(raw, "destructiveHint", None),
        "openWorldHint": getattr(raw, "openWorldHint", None),
    }


def test_tool_definitions_cover_all_registered_tools_and_parameters() -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")

    app = create_app()
    if isinstance(app, _FallbackServer):
        pytest.skip("backend unavailable")

    tools = asyncio.run(app.list_tools())
    registered = {
        tool.name: tool
        for tool in tools
        if tool.name.startswith("renforge_")
    }

    expected = set(TOOL_DEFINITIONS)
    assert set(registered) == expected
    assert len(registered) == 54

    for name, definition in TOOL_DEFINITIONS.items():
        tool = registered[name]
        assert tool.description and isinstance(tool.description, str)
        assert tool.description.strip()
        assert tool.description == definition.description

        annotations = _annotations_to_dict(tool.annotations)
        assert annotations == definition.annotations

        properties = tool.parameters["properties"]
        assert set(properties) == set(definition.parameters)
        for param, schema in properties.items():
            assert definition.parameters[param]
            assert isinstance(schema.get("description"), str)
            assert schema["description"].strip()


def test_emitted_tool_schemas_encode_options_limits_and_required_relationships() -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")

    tools = asyncio.run(create_app().list_tools())
    schemas = {
        tool.name: tool.parameters
        for tool in tools
        if tool.name.startswith("renforge_")
    }

    scan_sections = schemas["renforge_scan_project"]["properties"]["sections"]
    section_array = next(item for item in scan_sections["anyOf"] if item.get("type") == "array")
    assert section_array["items"]["enum"] == [
        "files",
        "variables",
        "graph",
        "labels",
        "jumps",
        "calls",
        "menus",
        "characters",
        "images",
        "unresolved_targets",
    ]

    launch = schemas["renforge_launch"]["properties"]
    assert launch["display"]["enum"] == ["auto", "native", "xvfb", "external", "none"]
    assert launch["audio"]["enum"] == ["auto", "native", "dummy", "none"]
    assert launch["persistent"]["enum"] == ["existing", "empty", "copy", "fixture"]
    assert launch["timeout"]["minimum"] == 0

    compact = schemas["renforge_game_state_compact"]["properties"]
    assert compact["state_profile"]["enum"] == ["minimal", "interaction", "debug", "full"]
    assert (compact["max_depth"]["minimum"], compact["max_depth"]["maximum"]) == (0, 20)
    assert (compact["max_items"]["minimum"], compact["max_items"]["maximum"]) == (1, 10_000)
    assert (compact["max_output_bytes"]["minimum"], compact["max_output_bytes"]["maximum"]) == (
        64,
        2_000_000,
    )

    send_input = schemas["renforge_send_input"]
    scroll = send_input["properties"]["scroll"]
    scroll_object = next(item for item in scroll["anyOf"] if item.get("type") == "object")
    assert scroll_object["required"] == ["x", "y", "direction"]
    assert scroll_object["additionalProperties"] is False
    assert scroll_object["properties"]["direction"]["enum"] == ["up", "down"]
    assert len(send_input["oneOf"]) == 3

    wait_schema = schemas["renforge_wait_until"]
    assert len(wait_schema["oneOf"]) == 3
    assert wait_schema["properties"]["timeout"]["maximum"] == 120

    assert schemas["renforge_get_errors"]["properties"]["since"]["minimum"] == 0

    scenario_steps = schemas["renforge_run_scenario"]["properties"]["steps"]
    assert len(scenario_steps["items"]["oneOf"]) == 14
    assert scenario_steps["items"]["oneOf"][0]["additionalProperties"] is False
    assert scenario_steps["items"]["oneOf"][0]["required"] == ["set"]


def test_invalid_enum_is_rejected_before_tool_implementation(monkeypatch, tmp_path) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client
    from renforge.tools import live

    calls: list[str] = []
    monkeypatch.setattr(
        live,
        "saves",
        lambda project_path, action, **kwargs: calls.append(action) or {"ok": True},
    )

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_saves",
                {"project_path": str(tmp_path), "action": "delete"},
                raise_on_error=False,
            )

    result = asyncio.run(_call())
    assert result.is_error is True
    assert calls == []
    assert "delete" in json.dumps([block.model_dump() for block in result.content])


def test_invalid_nested_scroll_is_rejected_before_tool_implementation(
    monkeypatch, tmp_path
) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    from fastmcp import Client
    from renforge.tools import live

    calls: list[dict] = []
    monkeypatch.setattr(
        live,
        "send_input",
        lambda project_path, **kwargs: calls.append(kwargs) or {"ok": True},
    )

    async def _call():
        async with Client(create_app()) as client:
            return await client.call_tool(
                "renforge_send_input",
                {
                    "project_path": str(tmp_path),
                    "scroll": {"x": 10, "y": 20, "direction": "sideways"},
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())
    assert result.is_error is True
    assert calls == []
    assert "sideways" in result.content[0].text


def test_high_risk_tool_guidance_includes_safety_notes() -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")

    app = create_app()
    if isinstance(app, _FallbackServer):
        pytest.skip("backend unavailable")

    tools = asyncio.run(app.list_tools())
    by_name = {tool.name: tool for tool in tools if tool.name.startswith("renforge_")}

    def _desc(name: str) -> str:
        return by_name[name].description or ""

    assert "Call this first" in _desc("renforge_info")
    assert "same full payload" in _desc("renforge_context").lower()
    assert "get_var" in _desc("renforge_eval")
    assert "prefer" in _desc("renforge_eval").lower()
    assert "overwrite" in _desc("renforge_saves").lower()
    assert "overwrite" in _desc("renforge_capture_screenshot").lower()
    assert "writes" in _desc("renforge_generate_translations").lower()
    assert "write" in _desc("renforge_web_build").lower()
    assert "open-world" not in _desc("renforge_web_build").lower()
    assert "write" in _desc("renforge_distribute").lower()

    annotations = _annotations_to_dict(by_name["renforge_eval"].annotations)
    assert annotations["destructiveHint"] is True


def test_tool_definitions_describe_real_side_effects_and_exact_options() -> None:
    definitions = TOOL_DEFINITIONS

    def description(name: str) -> str:
        return definitions[name].description.lower()

    def parameter(name: str, key: str) -> str:
        return definitions[name].parameters[key].lower()

    assert "lint output" in description("renforge_parse_lint")
    assert "does not run" in description("renforge_parse_lint")

    assert all(
        mode in parameter("renforge_launch", "display")
        for mode in ("auto", "native", "xvfb", "external", "none")
    )
    assert "headless" not in parameter("renforge_launch", "display")
    assert all(
        mode in parameter("renforge_launch", "audio")
        for mode in ("auto", "native", "dummy", "none")
    )
    assert "renforge_launch_status" in parameter("renforge_launch", "timeout")
    assert all(
        mode in parameter("renforge_launch", "persistent")
        for mode in ("existing", "empty", "copy", "fixture")
    )

    assert all(
        action in parameter("renforge_saves", "action")
        for action in ("save", "load", "list")
    )
    assert "delete" not in parameter("renforge_saves", "action")
    assert "clear" not in description("renforge_saves")
    assert "zero-based" in parameter("renforge_select_choice", "index")
    assert "one-based" not in parameter("renforge_select_choice", "index")

    assert "layout" in parameter("renforge_scene_tree", "detail")
    assert "wireframe" in parameter("renforge_scene_tree", "format")
    assert "yaml" not in parameter("renforge_scene_tree", "format")
    assert ".renforge/scenes" in description("renforge_scene_tree")
    assert "overwrite" in description("renforge_scene_tree")

    assert "arbitrary python" in description("renforge_wait_until")
    assert "repeated" in description("renforge_wait_until")
    assert "prefer" in description("renforge_wait_until")
    assert "quick_load" in description("renforge_control")
    assert "replaces" in description("renforge_control")
    assert "quit" in description("renforge_control")
    assert "stops" in description("renforge_control")

    assert all(
        mode in parameter("renforge_send_input", "key")
        for mode in ("enter", "esc", "pageup", "backspace", "function")
    )
    assert all(
        field in parameter("renforge_send_input", "scroll")
        for field in ("x", "y", "direction", "amount")
    )
    assert "only valid with text" in parameter("renforge_send_input", "submit")

    assert "integer" in parameter("renforge_position_element", "xpos")
    assert "fraction" in parameter("renforge_position_element", "xpos")
    assert ".rpy" in description("renforge_position_element")

    assert all(
        action in parameter("renforge_measure", "action")
        for action in ("align", "gap", "distribute", "center", "overlap", "fit", "contrast")
    )
    assert all(
        field in parameter("renforge_measure", "targets")
        for field in ("id", "x", "y", "width", "height")
    )
    assert "wcag" in description("renforge_measure")

    assert all(
        action in parameter("renforge_run_scenario", "steps")
        for action in (
            "set", "eval", "click", "click_at", "advance", "scroll", "wait", "assert",
            "select_choice", "capture", "save", "load", "control", "send_input",
        )
    )
    assert "600" in parameter("renforge_run_scenario", "timeout")
    assert all(
        profile in parameter("renforge_run_scenario", "state_profile")
        for profile in ("minimal", "interaction", "debug", "full")
    )

    assert "color" in parameter("renforge_scene_tree", "include")
    assert "style" in parameter("renforge_scene_tree", "include")
    assert "overflow" in parameter("renforge_scene_tree", "include")
    assert "20" in parameter("renforge_scene_tree", "max_output_depth")
    assert "10000" in parameter("renforge_scene_tree", "max_items")
    assert "2000000" in parameter("renforge_scene_tree", "max_output_bytes")

    assert "0 and 120" in parameter("renforge_wait_until", "timeout")
    assert all(
        profile in parameter("renforge_wait_until", "state_profile")
        for profile in ("minimal", "interaction", "debug", "full")
    )
    assert "after the runtime stops" in parameter("renforge_get_errors", "project_path")

    assert "dialogue.txt" in description("renforge_export_dialogue")
    assert "overwrite" in description("renforge_export_dialogue")
    export_annotations = definitions["renforge_export_dialogue"].annotations
    assert export_annotations["readOnlyHint"] is False
    assert export_annotations["destructiveHint"] is True


def test_tool_annotations_are_conservative_for_conditional_and_sdk_side_effects() -> None:
    pure_frame_reads = {
        "renforge_screenshot",
        "renforge_find_image_on_screen",
    }
    for name in pure_frame_reads:
        assert TOOL_DEFINITIONS[name].annotations == {
            "readOnlyHint": True,
            "idempotentHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        }

    assert TOOL_DEFINITIONS["renforge_scene_tree"].annotations == {
        "readOnlyHint": False,
        "idempotentHint": False,
        "destructiveHint": True,
        "openWorldHint": False,
    }
    assert TOOL_DEFINITIONS["renforge_wait_until"].annotations == {
        "readOnlyHint": False,
        "idempotentHint": False,
        "destructiveHint": True,
        "openWorldHint": True,
    }

    sdk_backed_tools = {
        "renforge_launch",
        "renforge_jump",
        "renforge_new_game",
        "renforge_autopilot",
        "renforge_translation_stats",
        "renforge_generate_translations",
        "renforge_export_dialogue",
        "renforge_web_build",
        "renforge_distribute",
        "renforge_search_docs",
        "renforge_get_doc",
        "renforge_list_docs",
    }
    for name in sdk_backed_tools:
        annotations = TOOL_DEFINITIONS[name].annotations
        assert annotations["readOnlyHint"] is False, name
        assert annotations["openWorldHint"] is True, name
        assert "download" in TOOL_DEFINITIONS[name].description.lower(), name


def test_risky_runtime_annotation_matrix_is_conservative() -> None:
    arbitrary_python_tools = {
        "renforge_eval",
        "renforge_get_var",
        "renforge_wait_until",
        "renforge_run_scenario",
    }
    arbitrary_game_action_tools = {
        "renforge_advance",
        "renforge_control",
        "renforge_send_input",
        "renforge_saves",
        "renforge_select_choice",
        "renforge_click_element",
        "renforge_hover_element",
        "renforge_click_at",
        "renforge_position_element",
        "renforge_set_var",
    }
    for name in arbitrary_python_tools | arbitrary_game_action_tools:
        annotations = TOOL_DEFINITIONS[name].annotations
        assert annotations["readOnlyHint"] is False, name
        assert annotations["idempotentHint"] is False, name
        assert annotations["destructiveHint"] is True, name
        assert annotations["openWorldHint"] is True, name

    eval_description = TOOL_DEFINITIONS["renforge_eval"].description.lower()
    assert all(capability in eval_description for capability in ("filesystem", "process", "network"))


def test_catalog_states_exact_runtime_and_filesystem_contracts() -> None:
    definitions = TOOL_DEFINITIONS
    context = definitions["renforge_context"].description.lower()
    assert "same full payload" in context
    assert "narrow" not in context

    launch = definitions["renforge_launch"]
    assert "file.rpy:line" in launch.parameters["warp"]
    assert "does not resolve label names" in launch.parameters["warp"].lower()
    assert "environment marker only" in launch.parameters["persistent"].lower()
    assert "game/" in launch.description
    assert ".renforge/control" in launch.description
    assert "arbitrary save directory" in launch.parameters["savedir"].lower()
    assert "only" in launch.parameters["cleanup_on_stop"].lower()
    assert "temporary" in launch.parameters["cleanup_on_stop"].lower()

    launch_status = definitions["renforge_launch_status"].description.lower()
    for status in ("idle", "starting", "ready", "failed", "closing", "closed"):
        assert f"`{status}`" in launch_status

    assert "case-insensitive substring" in definitions["renforge_select_choice"].description.lower()
    assert "optional" in definitions["renforge_click_element"].parameters["expected_frame_id"].lower()
    assert "may create" in definitions["renforge_scene_tree"].description.lower()
    assert ".renforge/scenes" in definitions["renforge_scene_tree"].description.lower()

    autopilot = definitions["renforge_autopilot"].description.lower()
    assert "fresh session" in autopilot
    assert ".renforge/autopilot.json" in autopilot
    assert "current state" not in autopilot

    web_build = definitions["renforge_web_build"].description.lower()
    assert "separately installed web dlc" in web_build
    assert "does not download external toolchains" in web_build


def _step_payloads(schemas: dict[str, dict]) -> dict[str, dict]:
    steps = schemas["renforge_run_scenario"]["properties"]["steps"]["items"]["oneOf"]
    return {item["required"][0]: item["properties"][item["required"][0]] for item in steps}


def test_optional_null_siblings_do_not_break_exclusive_tool_schemas(
    monkeypatch, tmp_path
) -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    pytest.importorskip("jsonschema")
    from fastmcp import Client
    from jsonschema import Draft202012Validator
    from renforge.tools import live

    send_calls: list[dict] = []
    wait_calls: list[dict] = []
    monkeypatch.setattr(
        live,
        "send_input",
        lambda project_path, **kwargs: send_calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(
        live,
        "wait_until",
        lambda project_path, **kwargs: wait_calls.append(kwargs) or {"ok": True, "matched": {}},
    )

    async def _call():
        async with Client(create_app()) as client:
            send = await client.call_tool(
                "renforge_send_input",
                {
                    "project_path": str(tmp_path),
                    "text": "hello",
                    "key": None,
                    "scroll": None,
                },
                raise_on_error=False,
            )
            wait = await client.call_tool(
                "renforge_wait_until",
                {
                    "project_path": str(tmp_path),
                    "label": "start",
                    "screen": None,
                    "expr": None,
                    "timeout": 1,
                },
                raise_on_error=False,
            )
            return send, wait

    send, wait = asyncio.run(_call())
    assert send.is_error is False, send.content
    assert wait.is_error is False, wait.content
    assert send_calls == [{"text": "hello", "key": None, "scroll": None, "submit": False}]
    assert wait_calls[0]["label"] == "start"
    assert wait_calls[0]["screen"] is None
    assert wait_calls[0]["expr"] is None

    tools = asyncio.run(create_app().list_tools())
    schemas = {tool.name: tool.parameters for tool in tools}
    send_schema = Draft202012Validator(schemas["renforge_send_input"])
    wait_schema = Draft202012Validator(schemas["renforge_wait_until"])
    send_schema.validate(
        {"project_path": str(tmp_path), "text": "hello", "key": None, "scroll": None}
    )
    wait_schema.validate(
        {"project_path": str(tmp_path), "label": "start", "screen": None, "expr": None}
    )
    saves_schema = Draft202012Validator(schemas["renforge_saves"])
    saves_schema.validate(
        {"project_path": str(tmp_path), "action": "list", "slot": None, "extra_info": None}
    )
    assert not saves_schema.is_valid(
        {"project_path": str(tmp_path), "action": "save", "slot": None, "regexp": None}
    )
    assert not saves_schema.is_valid(
        {"project_path": str(tmp_path), "action": "load", "slot": "", "regexp": None}
    )
    Draft202012Validator(schemas["renforge_select_choice"]).validate(
        {"project_path": str(tmp_path), "text": "Go", "index": None}
    )
    scenario_schema = Draft202012Validator(schemas["renforge_run_scenario"])
    scenario_schema.validate(
        {
            "project_path": str(tmp_path),
            "steps": [
                {"send_input": {"text": "hello", "key": None, "scroll": None}},
                {"wait": {"label": "start", "screen": None, "expr": None}},
                {"select_choice": {"text": "Go", "index": None}},
                {
                    "click": {
                        "text": "Go",
                        "id": None,
                        "target": None,
                        "screen": None,
                        "exact": None,
                        "element_id": None,
                        "expected_frame_id": None,
                    }
                },
            ],
        }
    )
    assert not send_schema.is_valid(
        {"project_path": str(tmp_path), "text": "hello", "key": "enter"}
    )


def test_scenario_step_schemas_match_runtime_contracts() -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")

    tools = asyncio.run(create_app().list_tools())
    schemas = {
        tool.name: tool.parameters
        for tool in tools
        if tool.name.startswith("renforge_")
    }
    payloads = _step_payloads(schemas)

    wait = payloads["wait"]
    assert wait["additionalProperties"] is False
    assert len(wait["oneOf"]) == 3
    assert {"label", "screen", "expr"} <= set(wait["properties"])

    send_input = payloads["send_input"]
    assert send_input["additionalProperties"] is False
    assert len(send_input["oneOf"]) == 3

    capture = payloads["capture"]
    string_branch = next(item for item in capture["oneOf"] if item.get("type") == "string")
    assert string_branch["not"]["enum"] == [".", ".."]
    object_branch = next(item for item in capture["oneOf"] if item.get("type") == "object")
    assert object_branch["properties"]["name"]["not"]["enum"] == [".", ".."]

    assert payloads["eval"]["oneOf"][1]["required"] == ["expr"]
    assert payloads["save"]["oneOf"][1]["required"] == ["slot"]
    assert payloads["load"]["oneOf"][1]["required"] == ["slot"]


def test_run_scenario_schema_rejects_payloads_runtime_rejects() -> None:
    pytest.importorskip("fastmcp", reason="fastmcp not installed")
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    tools = asyncio.run(create_app().list_tools())
    schema = next(tool.parameters for tool in tools if tool.name == "renforge_run_scenario")
    validator = Draft202012Validator(schema)

    def valid(steps: list[dict]) -> bool:
        return validator.is_valid({"project_path": "/tmp/game", "steps": steps})

    assert valid([{"wait": {"screen": "choice"}}])
    assert valid([{"send_input": {"text": "hello"}}])
    assert valid([{"capture": "frame"}])
    assert not valid([{"wait": {}}])
    assert not valid([{"click": {}}])
    assert not valid([{"click": {"text": None, "id": None}}])
    assert not valid([{"send_input": {"text": "a", "key": "enter"}}])
    assert not valid([{"capture": "."}])
    assert not valid([{"capture": {"name": ".."}}])


def test_get_var_description_does_not_promise_zero_user_code() -> None:
    description = TOOL_DEFINITIONS["renforge_get_var"].description.lower()
    assert "does not execute" not in description
    assert "type label" in description
    assert "repr" in description
    assert "property" in description
    assert TOOL_DEFINITIONS["renforge_get_var"].annotations == {
        "readOnlyHint": False,
        "idempotentHint": False,
        "destructiveHint": True,
        "openWorldHint": True,
    }
