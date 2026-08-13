from __future__ import annotations

from typing import Any

from .captures import capture_name_json_schema


_SCAN_SECTIONS = [
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
_STATE_PROFILES = ["minimal", "interaction", "debug", "full"]
_CONTROL_ACTIONS = [
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
]
_SCENARIO_ACTIONS = [
    "set",
    "eval",
    "click",
    "click_at",
    "advance",
    "scroll",
    "wait",
    "assert",
    "select_choice",
    "capture",
    "save",
    "load",
    "control",
    "send_input",
]


def _enum(*values: str) -> dict[str, Any]:
    return {"enum": list(values), "type": "string"}


def _limits(minimum: int | float, maximum: int | float) -> dict[str, Any]:
    return {"minimum": minimum, "maximum": maximum}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _oneof_present(
    required_props: dict[str, Any],
    *,
    null: tuple[str, ...] = (),
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match when required fields are present; omitted or JSON-null siblings are allowed."""
    properties = dict(required_props)
    for name in null:
        properties[name] = {"type": "null"}
    if extra:
        properties.update(extra)
    return {"required": list(required_props), "properties": properties}


_CAPTURE_NAME_SCHEMA = capture_name_json_schema()
_SCREENSHOT_PARAMETER_SCHEMAS = {
    "width": {"minimum": 0},
    "height": {"minimum": 0},
    "crop_x": {"minimum": 0},
    "crop_y": {"minimum": 0},
    "crop_width": {"minimum": 0},
    "crop_height": {"minimum": 0},
    "scale": _limits(0.1, 16.0),
    "grid": {"minimum": 0},
    "crosshair_x": {"minimum": -1},
    "crosshair_y": {"minimum": -1},
}
_SCROLL_SCHEMA = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "direction": _enum("up", "down"),
        "amount": {"type": "integer", "minimum": 1},
    },
    "required": ["x", "y", "direction"],
    "additionalProperties": False,
}
_SEND_INPUT_ONEOF = [
    _oneof_present({"text": {"type": "string"}}, null=("key", "scroll")),
    _oneof_present(
        {"key": {"type": "string", "minLength": 1}},
        null=("text", "scroll"),
        extra={"submit": {"const": False}},
    ),
    _oneof_present(
        {"scroll": _SCROLL_SCHEMA},
        null=("text", "key"),
        extra={"submit": {"const": False}},
    ),
]
_WAIT_UNTIL_ONEOF = [
    _oneof_present(
        {"label": {"type": "string", "minLength": 1}},
        null=("screen", "expr"),
    ),
    _oneof_present(
        {"screen": {"type": "string", "minLength": 1}},
        null=("label", "expr"),
    ),
    _oneof_present(
        {"expr": {"type": "string", "minLength": 1}},
        null=("label", "screen"),
    ),
]


def _scenario_step_schema(action: str) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {
        "set": {"type": "object", "minProperties": 1},
        "eval": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {"expr": {"type": "string", "minLength": 1}},
                    "required": ["expr"],
                    "additionalProperties": False,
                },
            ]
        },
        "click": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {
                        "text": _nullable({"type": "string", "minLength": 1}),
                        "id": _nullable({"type": "string", "minLength": 1}),
                        "target": _nullable({"type": "string", "minLength": 1}),
                        "screen": _nullable({"type": "string"}),
                        "exact": _nullable({"type": "boolean"}),
                        "element_id": _nullable({"type": "string", "minLength": 1}),
                        "expected_frame_id": {"type": ["string", "null"]},
                    },
                    "anyOf": [
                        {
                            "required": [selector],
                            "properties": {
                                selector: {"type": "string", "minLength": 1}
                            },
                        }
                        for selector in ("text", "id", "target", "element_id")
                    ],
                    "additionalProperties": False,
                },
            ]
        },
        "click_at": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "coordinate_space": _enum("logical", "screenshot"),
                "expected_frame_id": {"type": ["string", "null"]},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        "scroll": _SCROLL_SCHEMA,
        "wait": {
            "type": "object",
            "properties": {
                "label": _nullable({"type": "string", "minLength": 1}),
                "screen": _nullable({"type": "string", "minLength": 1}),
                "expr": _nullable({"type": "string", "minLength": 1}),
                "interval": {"type": "number", "minimum": 0},
                "state_profile": _enum(*_STATE_PROFILES),
                "include": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
            "oneOf": _WAIT_UNTIL_ONEOF,
        },
        "assert": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {
                        "expr": {"type": "string", "minLength": 1},
                        "equals": {},
                        "message": {"type": "string"},
                    },
                    "required": ["expr"],
                    "additionalProperties": False,
                },
            ]
        },
        "select_choice": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {
                        "text": _nullable({"type": "string", "minLength": 1}),
                        "index": _nullable({"type": "integer", "minimum": 0}),
                    },
                    "additionalProperties": False,
                    "oneOf": [
                        _oneof_present(
                            {"text": {"type": "string", "minLength": 1}},
                            extra={"index": {"anyOf": [{"type": "null"}, {"const": -1}]}},
                        ),
                        _oneof_present(
                            {"index": {"type": "integer", "minimum": 0}},
                            extra={"text": {"anyOf": [{"type": "null"}, {"const": ""}]}},
                        ),
                    ],
                },
            ]
        },
        "capture": {
            "oneOf": [
                _CAPTURE_NAME_SCHEMA,
                {
                    "type": "object",
                    "properties": {"name": _CAPTURE_NAME_SCHEMA},
                    "additionalProperties": False,
                },
            ]
        },
        "save": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {"slot": {"type": "string", "minLength": 1}},
                    "required": ["slot"],
                    "additionalProperties": False,
                },
            ]
        },
        "load": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {"slot": {"type": "string", "minLength": 1}},
                    "required": ["slot"],
                    "additionalProperties": False,
                },
            ]
        },
        "control": {
            "oneOf": [
                _enum(*_CONTROL_ACTIONS),
                {
                    "type": "object",
                    "properties": {"action": _enum(*_CONTROL_ACTIONS)},
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ]
        },
        "send_input": {
            "type": "object",
            "properties": {
                "text": _nullable({"type": "string"}),
                "key": _nullable({"type": "string", "minLength": 1}),
                "scroll": _nullable(_SCROLL_SCHEMA),
                "submit": {"type": "boolean"},
            },
            "additionalProperties": False,
            "oneOf": _SEND_INPUT_ONEOF,
        },
        "advance": {
            "oneOf": [
                {"type": "null"},
                {"type": "integer", "minimum": 1},
                {
                    "type": "object",
                    "properties": {"count": {"type": "integer", "minimum": 1}},
                    "additionalProperties": False,
                },
            ]
        },
    }
    return {
        "type": "object",
        "properties": {
            action: payloads[action],
            "timeout": {"type": "number", "minimum": 0},
            "step_timeout": {"type": "number", "minimum": 0},
        },
        "required": [action],
        "additionalProperties": False,
    }
