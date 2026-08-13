from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from typing_extensions import NotRequired, TypedDict

from .tool_schema import (
    _CAPTURE_NAME_SCHEMA,
    _CONTROL_ACTIONS,
    _SCAN_SECTIONS,
    _SCENARIO_ACTIONS,
    _SCREENSHOT_PARAMETER_SCHEMAS,
    _SCROLL_SCHEMA,
    _SEND_INPUT_ONEOF,
    _STATE_PROFILES,
    _WAIT_UNTIL_ONEOF,
    _enum,
    _limits,
    _oneof_present,
    _scenario_step_schema,
)


class ScrollInput(TypedDict):
    x: float
    y: float
    direction: Literal["up", "down"]
    amount: NotRequired[int]


@dataclass(frozen=True)
class ToolDefinition:
    description: str
    annotations: dict[str, bool]
    parameters: dict[str, str]
    parameter_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    parameter_types: dict[str, Any] = field(default_factory=dict)


def _ann(
    *,
    readOnlyHint: bool,
    idempotentHint: bool,
    destructiveHint: bool,
    openWorldHint: bool,
) -> dict[str, bool]:
    return {
        "readOnlyHint": readOnlyHint,
        "idempotentHint": idempotentHint,
        "destructiveHint": destructiveHint,
        "openWorldHint": openWorldHint,
    }


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "renforge_info": ToolDefinition(
        description=(
            "Call this first. It returns the active project target (dashboard, \"serve_default\" "
            "or cwd), plus local runtime hints and RenForge version. Use this to determine the "
            "project path you should pass to project-scoped tools."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={},
    ),
    "renforge_context": ToolDefinition(
        description=(
            "Return the same full payload as `renforge_info`, including active project discovery, "
            "dashboard/runtime hints, and RenForge version. This is an alias, not a reduced view."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={},
    ),
    "renforge_inspect_image": ToolDefinition(
        description=(
            "Inspect a local image file and optionally crop/zoom it before returning PNG bytes. "
            "Use this for artifact review without launching the game."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "image_path": "Local image path to load and inspect.",
            "crop_x": "Pixel offset from left for the crop rectangle origin.",
            "crop_y": "Pixel offset from top for the crop rectangle origin.",
            "crop_width": "Optional crop width in pixels; 0 keeps full width.",
            "crop_height": "Optional crop height in pixels; 0 keeps full height.",
            "scale": "Scale multiplier applied after crop; > 1 zooms in, < 1 zooms out.",
        },
        parameter_schemas={
            "crop_x": {"minimum": 0},
            "crop_y": {"minimum": 0},
            "crop_width": {"minimum": 0},
            "crop_height": {"minimum": 0},
            "scale": _limits(0.1, 16.0),
        },
    ),
    "renforge_inspect_project": ToolDefinition(
        description=(
            "Read-only static analysis of a Ren'Py project: scan metadata and file structure without "
            "starting the game engine. Use this for safe project triage before runtime actions."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Absolute or relative Ren'Py project root containing `game/`.",
        },
    ),
    "renforge_scan_project": ToolDefinition(
        description=(
            "Search project scripts with bounded output. Use `sections`, `file_glob`, and `symbol` for "
            "focused indexing, then paginate with `offset` and `limit` for stable batches."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root to index for static analysis.",
            "sections": (
                "Optional sections: `files`, `variables`, `graph`, `labels`, `jumps`, `calls`, `menus`, `characters`, "
                "`images`, and `unresolved_targets`."
            ),
            "file_glob": "Optional filename glob filter for targeted scan files.",
            "symbol": "Filter matches for this symbol; empty keeps section-wide scan.",
            "offset": "Zero-based pagination offset for scan results.",
            "limit": "Maximum items per page. Keep values bounded to avoid large payloads.",
        },
        parameter_schemas={
            "sections": {"items": _enum(*_SCAN_SECTIONS)},
            "offset": {"minimum": 0},
            "limit": _limits(1, 1_000),
        },
    ),
    "renforge_find_references": ToolDefinition(
        description=(
            "Find symbol definitions and references across scripts and templates with the same pagination "
            "controls as scan. Useful before editing or renaming project identifiers."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root where symbol references should be resolved.",
            "symbol": "Symbol name to resolve (for example label, define, or python identifier).",
            "file_glob": "Optional glob filter limiting candidate files.",
            "offset": "Zero-based pagination offset for matches.",
            "limit": "Maximum results to return; keep this bounded for large codebases.",
        },
        parameter_schemas={
            "symbol": {"pattern": r"^[A-Za-z_][A-Za-z0-9_]*$"},
            "offset": {"minimum": 0},
            "limit": _limits(1, 1_000),
        },
    ),
    "renforge_parse_lint": ToolDefinition(
        description=(
            "Parse existing Ren'Py lint output text and return normalized diagnostics. This does not run Ren'Py, "
            "does not execute code, and does not touch the runtime."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={"text": "Existing Ren'Py lint output text to parse."},
    ),
    "renforge_launch": ToolDefinition(
        description=(
            "Start or attach a running game session with Live Editor enabled by default (pass editor=false for non-editor "
            "runtime launch). Launch starts the background startup flow, usually returning while startup is still in progress; "
            "poll `renforge_launch_status` and capture with `renforge_screenshot` before interaction. "
            "It may download/cache the selected SDK, inject session-owned `.rpy` files and editor assets under `game/`, and "
            "publish authenticated ownership/bridge metadata under `.renforge/control/`. `renforge_stop` removes only owned "
            "session artifacts."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root passed to the launcher and runtime bridge.",
            "warp": "Optional Ren'Py `file.rpy:line` startup target; this launch tool does not resolve label names.",
            "version": "Launcher version selector, either `stable` or an explicit semantic version (for example `8.5.3`).",
            "editor": "Enable/disable live editor injection; default True for interactive work.",
            "display": (
                "Display strategy: `auto`, `native`, `xvfb`, or `external`. The accepted token `none` returns an error because "
                "Ren'Py requires a display surface."
            ),
            "audio": "Audio strategy: `auto`, `native`, `dummy`, or `none`; both `dummy` and `none` use SDL dummy audio.",
            "savedir": (
                "Optional save directory override. `temporary` creates an isolated temporary directory; any other non-default "
                "value is an arbitrary save directory path that is created if missing and is never removed by stop."
            ),
            "persistent": (
                "Persistent mode: `existing` preserves current persistent data and `empty` removes it in the isolated session; "
                "`copy` and `fixture` currently set an environment marker only and do not copy or load fixture data."
            ),
            "cleanup_on_stop": (
                "When true, stop removes only the temporary save directory created by `savedir=temporary`; arbitrary save "
                "directories and existing saves are not deleted."
            ),
            "timeout": (
                "Ren'Py background startup deadline in seconds (0 uses the launcher default). This does not control the MCP "
                "response wait; poll `renforge_launch_status` after a `starting` result."
            ),
        },
        parameter_schemas={
            "display": _enum("auto", "native", "xvfb", "external", "none"),
            "audio": _enum("auto", "native", "dummy", "none"),
            "persistent": _enum("existing", "empty", "copy", "fixture"),
            "timeout": {"minimum": 0},
        },
    ),
    "renforge_launch_status": ToolDefinition(
        description=(
            "Poll launch progress and return `idle`, `starting`, `ready`, `failed`, `closing`, or `closed`. Use after any "
            "launch start when the first call has not returned an immediate ready state."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={"project_path": "Project root currently being launched or running."},
    ),
    "renforge_jump": ToolDefinition(
        description=(
            "Restart into a specific label/file position and keep interacting in a fresh game session. If needed, this may "
            "download and cache the required SDK before restart. Use for deterministic positioning before scripted checks."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root containing the target game.",
            "target": "Label name or `file:line` destination for relaunch.",
            "version": "Runtime version to use when restarting at target.",
        },
    ),
    "renforge_new_game": ToolDefinition(
        description=(
            "Restart at `start` and begin a new story progression. This may download and cache the required SDK before the "
            "fresh launch. Prefer this for clean scenario entry rather than reusing unstable runtime state."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root containing the game to restart.",
            "version": "Runtime version used for the fresh launch.",
        },
    ),
    "renforge_stop": ToolDefinition(
        description=(
            "Stop the live runtime and cleanup the launch/session state. Running games may be interrupted, and "
            "active quick-save/lock state should be considered volatile after stop."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=False,
        ),
        parameters={"project_path": "Project root whose active launch should be stopped."},
    ),
    "renforge_game_state": ToolDefinition(
        description=(
            "Read full live state for debugging and monitoring. Use this before/after interactions to verify "
            "state deltas without changing variables directly. Request optional sections such as `metrics` and `audio`."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with an active live session.",
            "include": "Optional sections to include in addition to default state (for example metrics, audio).",
        },
        parameter_schemas={"include": {"items": _enum("metrics", "audio")}},
    ),
    "renforge_game_state_compact": ToolDefinition(
        description=(
            "Read bounded live state with explicit variable limits and payload caps for large sessions. "
            "Use this in loops and scripts to keep responses predictable."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with an active live session.",
            "variable_names": "Restrict output to these variable names.",
            "variable_prefix": "Filter variable names by this prefix.",
            "state_profile": "Compact shape policy: `minimal`, `interaction`, `debug`, or `full`.",
            "max_depth": "Maximum object nesting depth to serialize, from 0 through 20.",
            "max_items": "Maximum variable count before trimming, from 1 through 10000.",
            "max_output_bytes": "Payload cap in bytes, from 64 through 2000000.",
        },
        parameter_schemas={
            "state_profile": _enum(*_STATE_PROFILES),
            "max_depth": _limits(0, 20),
            "max_items": _limits(1, 10_000),
            "max_output_bytes": _limits(64, 2_000_000),
        },
    ),
    "renforge_inspect_screen": ToolDefinition(
        description=(
            "Inspect a named active screen, including layer, scope and arguments. Useful for branch verification "
            "before choosing click or hover selectors."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active game session.",
            "name": "Screen name to inspect in the live bridge state.",
        },
    ),
    "renforge_advance": ToolDefinition(
        description=(
            "Advance one dialogue event from the currently active queue. Prefer `renforge_control(action=\"advance\")` "
            "only when you intentionally need control-command consistency with other navigation actions."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={"project_path": "Project root of the running live session."},
    ),
    "renforge_control": ToolDefinition(
        description=(
            "Run a runtime control action (`advance`, `rollback`, `toggle_skip`, `toggle_auto`, `toggle_afm`, `game_menu`, "
            "`hide_windows`, `quick_save`, `quick_load`, `reload_script`, `restart_interaction`, `quit`). "
            "Use `quick_load` with care: it replaces live state. `quit` stops the active game session. "
            "`renforge_advance` is a narrower alternative for pure advance actions. Destructive actions require "
            "`authorize=true` when `RENFORGE_POLICY=enforce`."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with running live session.",
            "action": "Control verb name to execute.",
            "interaction_id": "Optional interaction correlation id used for safety checks and logs.",
            "wait_for_effect": "Wait until bridge reports visible state effect before returning.",
            "effect_timeout": "Maximum seconds to wait when `wait_for_effect` is enabled.",
            "authorize": (
                "Explicitly authorize destructive actions when `RENFORGE_POLICY=enforce`; "
                "ignored for lower-risk actions."
            ),
        },
        parameter_schemas={"action": _enum(*_CONTROL_ACTIONS)},
    ),
    "renforge_send_input": ToolDefinition(
        description=(
            "Send exactly one input command path (text, key, or scroll object) into the live game. Keep calls scoped and "
            "single-purpose; for deterministic workflows prefer one action per invocation."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "text": "Text to inject as character-by-character text input.",
            "key": (
                "Readable named key such as `enter`, `esc`, an arrow, `pageup`, `pagedown`, `backspace`, `delete`, `home`, "
                "`end`, `space`, `tab`, or a function key."
            ),
            "scroll": (
                "Logical-coordinate object `{\"x\": number, \"y\": number, \"direction\": \"up\"|\"down\", "
                "\"amount\": integer?}`."
            ),
            "submit": "Press Enter after injected text; only valid with text input, not key or scroll.",
        },
        parameter_schemas={"scroll": _SCROLL_SCHEMA},
        input_schema={"oneOf": _SEND_INPUT_ONEOF},
        parameter_types={"scroll": ScrollInput | None},
    ),
    "renforge_saves": ToolDefinition(
        description=(
            "List, load, or save named slots in a single command family. `list` is read-only; `load` replaces live state "
            "and `save` can overwrite the chosen slot. Verify slot names before mutating gameplay progress. `load` requires "
            "`authorize=true` when `RENFORGE_POLICY=enforce`."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root of the running live game.",
            "action": "One of `list`, `load`, or `save`.",
            "slot": "Slot label used by save/load actions.",
            "extra_info": "Optional save metadata for save actions.",
            "regexp": "Optional regex filter for list only, selecting matching slots.",
            "authorize": (
                "Explicitly authorize `load` when `RENFORGE_POLICY=enforce`; ignored for list/save."
            ),
        },
        parameter_schemas={"action": _enum("save", "load", "list")},
        input_schema={
            "oneOf": [
                {
                    "properties": {
                        "action": {"const": "save"},
                        "slot": {"type": "string", "minLength": 1},
                        "regexp": {"type": "null"},
                    },
                    "required": ["action", "slot"],
                },
                {
                    "properties": {
                        "action": {"const": "load"},
                        "slot": {"type": "string", "minLength": 1},
                        "extra_info": {"type": "null"},
                        "regexp": {"type": "null"},
                    },
                    "required": ["action", "slot"],
                },
                {
                    "properties": {
                        "action": {"const": "list"},
                        "slot": {"type": "null"},
                        "extra_info": {"type": "null"},
                    },
                    "required": ["action"],
                },
            ]
        },
    ),
    "renforge_list_choices": ToolDefinition(
        description=(
            "List on-screen menu choices with index and text ordering. Call before `renforge_select_choice` to pick valid entries "
            "for the current menu state."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={"project_path": "Project root of the running live session."},
    ),
    "renforge_select_choice": ToolDefinition(
        description=(
            "Select a menu choice by case-insensitive text or zero-based index. Text matching prefers an exact match, then "
            "uses the shortest case-insensitive substring match. Use list output first to avoid ambiguous partial text."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root of the running live session.",
            "text": "Visible text to match; preferred over index.",
            "index": "Zero-based choice index fallback when text is omitted.",
        },
        input_schema={
            "oneOf": [
                _oneof_present(
                    {"text": {"type": "string", "minLength": 1}},
                    extra={"index": {"anyOf": [{"type": "null"}, {"const": -1}]}},
                ),
                _oneof_present(
                    {"index": {"type": "integer", "minimum": 0}},
                    extra={"text": {"anyOf": [{"type": "null"}, {"const": ""}]}},
                ),
            ]
        },
    ),
    "renforge_list_ui_elements": ToolDefinition(
        description=(
            "List focusable UI elements with bounds and element metadata. Use this first for semantic interaction planning; "
            "it is narrower than a full scene readout and aligned to click/hover workflows."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "screen": "Optional active screen filter.",
            "text": "Optional text filter to narrow focusable candidates.",
            "element_type": "Optional element type/class filter (for example button, label).",
        },
    ),
    "renforge_click_element": ToolDefinition(
        description=(
            "Click a semantic UI element by text/id/screen (preferred). Falls back safely through fresh frame guards and "
            "returns focus hints when another element captures the hit test. Use `renforge_click_at` only as coordinate fallback."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "text": "Preferred visible text matcher for the target control.",
            "element_id": "Exact control id override when text is unstable.",
            "screen": "Optional screen name filter to scope semantic lookup.",
            "exact": "Enable strict text matching.",
            "expected_frame_id": "Optional frame id from a recent scene/listing call; when supplied it rejects a stale frame.",
            "interaction_id": "Optional interaction correlation id for logs/troubleshooting.",
            "wait_for_effect": "Wait for a visual effect/state update before returning.",
            "effect_timeout": "How long to wait when `wait_for_effect` is true.",
        },
    ),
    "renforge_hover_element": ToolDefinition(
        description=(
            "Move cursor over a semantic control by text/id/screen. Prefer this before click when checking hover states "
            "or tooltips."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "text": "Text matcher for the control to hover.",
            "element_id": "Element id matcher for precise targeting.",
            "screen": "Screen filter for ambiguous UI states.",
            "exact": "Require exact text match.",
            "expected_frame_id": "Frame id expected from recent scene/listing calls.",
        },
    ),
    "renforge_get_ui_element_bounds": ToolDefinition(
        description=(
            "Resolve semantic element bounds without clicking. Use this after list output to verify target geometry before "
            "any pointer action."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "text": "Text matcher for the element.",
            "element_id": "Element id matcher for a precise target.",
            "screen": "Optional screen filter for targeted lookup.",
            "exact": "Require exact text matching.",
            "expected_frame_id": "Optional frame guard token for freshness.",
        },
    ),
    "renforge_click_at": ToolDefinition(
        description=(
            "Click a coordinate in screenshot or logical space. Use semantic click first (`renforge_click_element`) and fall back "
            "when text/id selectors are unavailable. Combine with coordinate space and frame/state guards."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "x": "Horizontal coordinate in selected coordinate space.",
            "y": "Vertical coordinate in selected coordinate space.",
            "expected_frame_id": "Frame id that must match for safety checks.",
            "expected_state": "Optional expected runtime state dictionary before click.",
            "coordinate_space": "`logical` (default) or `screenshot` coordinates.",
        },
        parameter_schemas={"coordinate_space": _enum("logical", "screenshot")},
    ),
    "renforge_get_displayable_bounds": ToolDefinition(
        description=(
            "Read placement bounds for a rendered displayable tag by tag name. Use this to diagnose layout changes "
            "or anchor calculations before moving elements."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "tag": "Displayable tag name to resolve.",
            "layer": "Optional Ren'Py layer override.",
        },
    ),
    "renforge_position_element": ToolDefinition(
        description=(
            "Reposition a shown image tag live and return new logical bounds. This mutates runtime placement only; after "
            "converging with scene/measure tools, write the final values into the `.rpy` source."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "tag": "Displayable tag to reposition.",
            "xpos": "X position: an integer is absolute logical pixels; a float is a screen fraction (for example 0.5).",
            "ypos": "Y position: an integer is absolute logical pixels; a float is a screen fraction (for example 0.5).",
            "xanchor": "Anchor point for x placement (0..1).",
            "yanchor": "Anchor point for y placement (0..1).",
            "xalign": "Alternative x alignment offset.",
            "yalign": "Alternative y alignment offset.",
            "xoffset": "Relative x offset in pixels.",
            "yoffset": "Relative y offset in pixels.",
            "zoom": "Scale factor for the displayable.",
            "rotate": "Rotation in degrees.",
            "layer": "Optional Ren'Py layer override.",
        },
    ),
    "renforge_diff_screenshots": ToolDefinition(
        description=(
            "Diff two PNG frames and return whether pixels changed plus the minimal changed rectangle. Use for regression checks "
            "between pre/post actions."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root used for path resolution of before/after images.",
            "before_path": "Path to baseline PNG file.",
            "after_path": "Path to comparison PNG; omit to compare against current live frame.",
            "threshold": "Pixel-difference threshold; higher values reduce sensitivity.",
        },
        parameter_schemas={"threshold": _limits(0, 255)},
    ),
    "renforge_eval": ToolDefinition(
        description=(
            "Evaluate an arbitrary Python expression in the running store namespace. This may call project functions and "
            "mutate runtime state or access the filesystem, start processes, and use the network with the game process's "
            "permissions. Prefer `renforge_get_var` for plain reads and use this only for controlled diagnostics. Pass "
            "`authorize=true` when `RENFORGE_POLICY=enforce`, unless this operation is allowlisted."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "expr": "Python expression to evaluate in the store namespace.",
            "authorize": (
                "Explicitly authorize this open-world expression when `RENFORGE_POLICY=enforce`."
            ),
        },
    ),
    "renforge_set_var": ToolDefinition(
        description=(
            "Write a game variable directly in the live store namespace. This mutates state immediately and should be used "
            "after validating the current state snapshot."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "name": "Variable name to set.",
            "value": "Value to write to the variable (JSON-compatible serializable object).",
        },
    ),
    "renforge_get_var": ToolDefinition(
        description=(
            "Read one named variable from the live store. JSON-safe values are returned as-is; other objects are replaced "
            "by a type label without calling conversion hooks such as `__repr__`. Resolving the name can still run a store "
            "property getter. Prefer this over `renforge_eval` when you only need a named lookup."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={"project_path": "Project root with active live session.", "name": "Variable name to read."},
    ),
    "renforge_poll_events": ToolDefinition(
        description=(
            "Read queued runtime events since a stream index. Use this for deterministic event-driven debugging instead "
            "of polling live UI repeatedly."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "since": "Exclusive cursor position for pagination of event history.",
        },
    ),
    "renforge_get_errors": ToolDefinition(
        description=(
            "Read recent runtime and bridge errors. Run this after risky actions or after a stop to confirm the game did not "
            "crash with hidden diagnostics."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root; this can still read bounded crash files after the runtime stops.",
            "since": "Exclusive cursor for paginating older logs/diagnostics.",
        },
        parameter_schemas={"since": {"minimum": 0}},
    ),
    "renforge_wait_until": ToolDefinition(
        description=(
            "Wait for one stable condition (`label`, `screen`, or `expr`). Prefer `label` or `screen` when possible; "
            "`expr` is arbitrary Python evaluated repeatedly in the store namespace and may mutate game state, access the "
            "filesystem, start processes, or use the network with the game process's permissions. Keep timeout bounded."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "label": "Target label to wait for; provide either label, screen, or expression.",
            "screen": "Target screen name to wait for.",
            "expr": "Expression condition to evaluate and await success. Evaluated repeatedly during waiting.",
            "timeout": "Maximum wait time, a finite value between 0 and 120 seconds.",
            "interval": "Polling interval in seconds.",
            "state_profile": "Returned state shape: `minimal`, `interaction`, `debug`, or `full`.",
            "include": "Optional fields to include in compact state output.",
            "max_depth": "Maximum object nesting depth in compact state, from 0 through 20.",
            "max_items": "Maximum list/map items in compact state, from 1 through 10000.",
            "max_output_bytes": "Payload cap for the result, from 64 through 2000000 bytes.",
        },
        parameter_schemas={
            "timeout": _limits(0, 120),
            "interval": {"minimum": 0},
            "state_profile": _enum(*_STATE_PROFILES),
            "max_depth": _limits(0, 20),
            "max_items": _limits(1, 10_000),
            "max_output_bytes": _limits(64, 2_000_000),
        },
        input_schema={"oneOf": _WAIT_UNTIL_ONEOF},
    ),
    "renforge_hit_test": ToolDefinition(
        description=(
            "Inspect which interactive nodes sit under a coordinate. Use this when click targets look stale or overlays may absorb "
            "events, and prefer semantic selectors once ambiguity is resolved."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "x": "X coordinate for hit testing.",
            "y": "Y coordinate for hit testing.",
            "coordinate_space": "Coordinate system for the test point (`logical` or `screenshot`).",
        },
        parameter_schemas={"coordinate_space": _enum("logical", "screenshot")},
    ),
    "renforge_scene_tree": ToolDefinition(
        description=(
            "Read the full scene graph (logical scene data). Unlike `renforge_list_ui_elements` this is broad and includes non-"
            "focusable nodes, so use it for full-tree inspection when semantic selectors are ambiguous. `save_as` or "
            "`diff_against` may create `.renforge/scenes/`; `save_as` writes and overwrites the named JSON snapshot. Omit both "
            "options for read-only inspection."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "detail": "Detail granularity (`semantic`, `layout`, or `raw`) for scene nodes.",
            "layers": "Optional layer filters.",
            "types": "Optional node type filters.",
            "screen": "Optional active screen filter.",
            "ids": "Optional element id filters.",
            "include": "Optional expensive node properties: `color`, `style`, and `overflow`.",
            "format": "Return format (`json` or `wireframe`).",
            "save_as": "Optional snapshot name saved under `.renforge/scenes/<name>.json`; reusing a name overwrites it.",
            "diff_against": "Optional snapshot name for diff context; compare against existing `.renforge/scenes/<name>.json` entry.",
            "max_output_depth": "Maximum traversal depth before truncation, from 0 through 20.",
            "max_items": "Maximum total nodes, from 1 through 10000.",
            "max_output_bytes": "Payload cap, from 64 through 2000000 bytes.",
        },
        parameter_schemas={
            "detail": _enum("semantic", "layout", "raw"),
            "include": {"items": _enum("color", "style", "overflow")},
            "format": _enum("json", "wireframe"),
            "save_as": {"pattern": r"^$|^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"},
            "diff_against": {"pattern": r"^$|^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"},
            "max_output_depth": _limits(0, 20),
            "max_items": _limits(1, 10_000),
            "max_output_bytes": _limits(64, 2_000_000),
        },
    ),
    "renforge_measure": ToolDefinition(
        description=(
            "Measure geometry between scene nodes without visual inspection. `contrast` samples the live frame and reports a "
            "WCAG ratio. Use this to detect layout drift after positional edits."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "action": "One of `align`, `gap`, `distribute`, `center`, `overlap`, `fit`, or `contrast`.",
            "targets": (
                "Scene node `id` strings or literal bounds objects `{\"x\": number, \"y\": number, \"width\": number, "
                "\"height\": number}`."
            ),
            "within": "Optional scene node id or literal `{x,y,width,height}` bounds used as the containing region.",
            "tolerance": "Optional tolerance; adds a pass verdict (for contrast, the minimum WCAG ratio).",
        },
        parameter_schemas={
            "action": _enum("align", "gap", "distribute", "center", "overlap", "fit", "contrast")
        },
    ),
    "renforge_run_scenario": ToolDefinition(
        description=(
            "Run a scripted scenario pipeline of multiple interaction steps in one call and return grouped diagnostics. "
            "`eval`, `assert`, and expression-based `wait` steps execute arbitrary Python that may mutate state, access the "
            "filesystem, start processes, or use the network. Validate expectations and review capture output on failure. "
            "Destructive or open-world steps require `authorize=true` when `RENFORGE_POLICY=enforce`."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root of the live session.",
            "steps": (
                "Ordered action objects. Supported actions: `set`, `eval`, `click`, `click_at`, `advance`, `scroll`, `wait`, "
                "`assert`, `select_choice`, `capture`, `save`, `load`, `control`, and `send_input`."
            ),
            "name": "Scenario name used for logs and output grouping.",
            "timeout": "Total finite wall-time budget from 0 through 600 seconds.",
            "stop_on_failure": "Abort remaining steps when one step fails.",
            "state_profile": "Compact state profile used for captures: `minimal`, `interaction`, `debug`, or `full`.",
            "capture_on_failure": "Capture diagnostics (including image) when step evaluation fails.",
            "authorize": (
                "Explicitly authorize a destructive or open-world step when `RENFORGE_POLICY=enforce`."
            ),
        },
        parameter_schemas={
            "steps": {
                "items": {
                    "oneOf": [
                        _scenario_step_schema(action) for action in _SCENARIO_ACTIONS
                    ]
                }
            },
            "timeout": _limits(0, 600),
            "state_profile": _enum(*_STATE_PROFILES),
        },
    ),
    "renforge_autopilot": ToolDefinition(
        description=(
            "Auto-play branches by stopping any current manual session and launching a fresh session for every replay. It writes "
            "incremental progress to `.renforge/autopilot.json` and may download/cache the required SDK. Best for broad checks; "
            "not deterministic and potentially expensive."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root for the live session.",
            "max_runs": "Maximum scenario runs before stopping.",
            "max_steps": "Maximum steps per run.",
        },
    ),
    "renforge_assets": ToolDefinition(
        description=(
            "Run static asset validation and return missing/orphaned image or audio references. Useful before build and before "
            "submitting translation/build changes."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={"project_path": "Project root to inspect."},
    ),
    "renforge_languages": ToolDefinition(
        description=(
            "List translation languages currently present under `game/tl/`. Use this before generating or exporting localized files."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={"project_path": "Project root for translation inventory."},
    ),
    "renforge_translation_stats": ToolDefinition(
        description=(
            "Compute missing/covered translation stats for a language before generation. Run this first, then gate calls to "
            "translation generation. This may download and cache the required SDK before analysis."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root to analyze.",
            "language": "Language code or name under `game/tl/`.",
        },
    ),
    "renforge_generate_translations": ToolDefinition(
        description=(
            "Generate or update translation files under `game/tl/<language>/`. This may download and cache the SDK before "
            "writing output. It writes files, so treat it as destructive when run against production trees and prefer checking "
            "stats first."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root to write translation files for.",
            "language": "Language code/name to generate.",
        },
    ),
    "renforge_export_dialogue": ToolDefinition(
        description=(
            "Export dialogue for translators or external tooling. This may download and cache the SDK, then writes "
            "and overwrites `dialogue.txt` at the project root."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root whose dialogue should be exported.",
            "language": "Optional language variant to export.",
        },
    ),
    "renforge_web_build": ToolDefinition(
        description=(
            "Run the Ren'Py web build pipeline and write browser assets. It may download/cache the Ren'Py SDK, but requires the "
            "separately installed Web DLC and does not download external toolchains or the Web DLC. Destination controls output."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root for build output.",
            "destination": "Optional output directory override.",
        },
    ),
    "renforge_distribute": ToolDefinition(
        description=(
            "Build desktop distribution packages and write them to destination. This executes packaging toolchains and may "
            "download and cache the SDK; it can be slow or destructive when output paths are reused."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=True,
        ),
        parameters={
            "project_path": "Project root to package.",
            "package": "Package target (for example `pc`, `mac`, `linux`).",
            "destination": "Optional output directory override.",
        },
    ),
    "renforge_search_docs": ToolDefinition(
        description=(
            "Search local Ren'Py documentation snippets by keyword before opening results. Use this to identify canonical topic IDs "
            "before `renforge_get_doc`. This may download and cache the SDK when docs are not already available."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
        parameters={"query": "Search query for Ren'Py documentation corpus."},
    ),
    "renforge_get_doc": ToolDefinition(
        description=(
            "Read a single Ren'Py documentation topic. For discovery, run `renforge_search_docs` and `renforge_list_docs` first "
            "to avoid guessing IDs. This may download and cache the SDK before resolving doc content."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
        parameters={"topic": "Topic identifier or slug in the doc catalog."},
    ),
    "renforge_list_docs": ToolDefinition(
        description=(
            "List available Ren'Py documentation topics. Use this as the discovery step before any topic-specific docs read. "
            "This may download and cache the SDK if docs are not already available."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
        parameters={},
    ),
    "renforge_screenshot": ToolDefinition(
        description=(
            "Capture the current live frame and return an in-memory image response (no persistent file write). Use this to inspect "
            "UI state before clicks or scenario actions."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "width": "Target capture width. Set one axis and keep aspect ratio via runtime logic.",
            "height": "Target capture height. Set one axis and keep aspect ratio via runtime logic.",
            "crop_x": "Crop origin x in logical coordinates.",
            "crop_y": "Crop origin y in logical coordinates.",
            "crop_width": "Crop width; 0 disables cropping in X.",
            "crop_height": "Crop height; 0 disables cropping in Y.",
            "scale": "Scale factor applied after crop/resize.",
            "grid": "Guide overlay grid spacing in pixels.",
            "crosshair_x": "Crosshair x coordinate; both x/y must be set together.",
            "crosshair_y": "Crosshair y coordinate; both x/y must be set together.",
            "rulers": "Draw rulers along frame edges for measurement.",
        },
        parameter_schemas=_SCREENSHOT_PARAMETER_SCHEMAS,
    ),
    "renforge_capture_screenshot": ToolDefinition(
        description=(
            "Persist the current frame under `.renforge/captures/` and return the output path plus hash. This writes disk and may "
            "overwrite an existing name."
        ),
        annotations=_ann(
            readOnlyHint=False,
            idempotentHint=False,
            destructiveHint=True,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "name": "Capture base filename (letters, digits, dot, dash, underscore).",
            "width": "Target capture width.",
            "height": "Target capture height.",
            "crop_x": "Crop origin x in logical coordinates.",
            "crop_y": "Crop origin y in logical coordinates.",
            "crop_width": "Crop width; 0 disables cropping in X.",
            "crop_height": "Crop height; 0 disables cropping in Y.",
            "scale": "Scale factor applied after crop/resize.",
            "grid": "Guide overlay grid spacing in pixels.",
            "crosshair_x": "Crosshair x coordinate; both x/y must be set together.",
            "crosshair_y": "Crosshair y coordinate; both x/y must be set together.",
            "rulers": "Draw rulers along frame edges for measurement.",
        },
        parameter_schemas={
            "name": {
                "pattern": _CAPTURE_NAME_SCHEMA["pattern"],
                "not": _CAPTURE_NAME_SCHEMA["not"],
            },
            **_SCREENSHOT_PARAMETER_SCHEMAS,
        },
    ),
    "renforge_estimate_translation": ToolDefinition(
        description=(
            "Estimate geometric translation between two images for visual text alignment workflows. This is file-only and read-only."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "before_path": "Path to baseline image.",
            "after_path": "Path to comparison image.",
            "region_x": "Optional crop region X origin.",
            "region_y": "Optional crop region Y origin.",
            "region_width": "Optional crop region width.",
            "region_height": "Optional crop region height.",
            "threshold": "Similarity threshold for matching pixels.",
            "max_shift": "Maximum per-axis shift considered during matching.",
        },
        parameter_schemas={
            "region_x": {"minimum": 0},
            "region_y": {"minimum": 0},
            "region_width": {"minimum": 0},
            "region_height": {"minimum": 0},
            "threshold": _limits(0, 255),
            "max_shift": _limits(0, 256),
        },
    ),
    "renforge_find_image_on_screen": ToolDefinition(
        description=(
            "Find a template image on the current frame and return matches with bounds in screenshot coordinates. Use this to "
            "close the control loop after screenshot capture and click fallback."
        ),
        annotations=_ann(
            readOnlyHint=True,
            idempotentHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
        parameters={
            "project_path": "Project root with active live session.",
            "template_path": "Template image path relative to project or absolute.",
            "threshold": "Match confidence threshold.",
            "max_matches": "Maximum number of matches to return.",
            "region_x": "Optional region x origin to limit search area.",
            "region_y": "Optional region y origin to limit search area.",
            "region_width": "Optional region width; set width/height together.",
            "region_height": "Optional region height; set width/height together.",
        },
        parameter_schemas={
            "threshold": _limits(0.0, 1.0),
            "max_matches": _limits(1, 100),
            "region_x": {"minimum": 0},
            "region_y": {"minimum": 0},
            "region_width": {"minimum": 0},
            "region_height": {"minimum": 0},
        },
    ),
}
