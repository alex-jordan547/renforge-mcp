# RenForge MCP

RenForge is a [Model Context Protocol](https://modelcontextprotocol.io/) server
for Ren'Py projects. An agent can inspect a project, start a game, observe a
frame, click a control, and verify runtime state without guessing the project's
structure or screen coordinates.

## Installation

The MCP server uses `stdio` and starts with:

```bash
uvx renforge@latest serve
```

The `@latest` suffix makes `uvx` fetch the newest published release on each
start instead of reusing a cached older build, so new tools appear without a
manual `uv cache clean`. Use `uvx renforge serve` to pin the cached build.

For a persistent installation with the dashboard:

```bash
pipx install "renforge[ui]"
renforge serve
```

Install `renforge` without `[ui]` only when the stdio MCP server is all that is
needed. The dashboard command requires the optional UI dependencies.

To develop RenForge:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[ui,test]"
renforge serve
```

The dashboard is a separate process:

```bash
renforge ui --project /path/to/game
```

When the dashboard is running, a `renforge_launch` request from an MCP client
is delegated to it. This is particularly useful with WSLg or when the MCP
client does not have direct access to a graphical display.

## Client configuration

The command is the same for every client. Example JSON configuration:

```json
{
  "mcpServers": {
    "renforge": {
      "command": "uvx",
      "args": ["renforge@latest", "serve"]
    }
  }
}
```

For Codex CLI:

```bash
codex mcp add renforge -- uvx renforge@latest serve
```

Or add this to `~/.codex/config.toml`:

```toml
[mcp_servers.renforge]
command = "uvx"
args = ["renforge@latest", "serve"]
```

For Claude Code:

```bash
claude mcp add renforge -- uvx renforge@latest serve
```

Every project-related tool takes `project_path`. Call `renforge_info` first
when possible: it returns `active_project` with a `project_source` explaining
how it was resolved — the dashboard selection, the `serve --project` default,
or a Ren'Py project auto-detected from the current directory. A null
`active_project` only means auto-discovery found nothing: every tool still
accepts `project_path` directly (no dashboard required), so ask the user for
the game's path and continue.

## Recommended workflow

```text
renforge_info
  -> active_project
renforge_launch(project_path)
  -> game and bridge are available
renforge_game_state_compact(project_path)
  -> current label and bounded state
renforge_game_state(project_path, include=["metrics", "audio"])
  -> optional render/cache/window metrics and per-channel audio state
renforge_inspect_screen(project_path, name="say")
  -> active layer, JSON-safe scope, and passed screen arguments
renforge_list_ui_elements(project_path)
  -> visible controls and frame_id
renforge_hover_element(..., expected_frame_id=frame_id)
  -> move over a control without clicking
renforge_click_element(..., expected_frame_id=frame_id)
  -> safe interaction
```

After editing a running project's `.rpy` file, hot-reload it and inspect the
result without restarting the game process:

```text
edit game/script.rpy
renforge_control(project_path, action="reload_script")
renforge_screenshot(project_path)
```

For image-driven interaction:

```text
renforge_find_image_on_screen(project_path, template_path)
  -> matches[], coordinate_space="screenshot", frame_id
match = matches[0]
renforge_click_at(
  project_path,
  x=match.center.x,
  y=match.center.y,
  coordinate_space="screenshot",
  expected_frame_id=frame_id,
)
```

`frame_id` protects against clicking a stale frame. If the game changes between
observation and click, RenForge returns a guard error and the agent must inspect
the current frame before trying again.

`renforge_list_ui_elements` returns Ren'Py logical coordinates. Bounds returned
by `renforge_find_image_on_screen` use the captured PNG's coordinates, so pass
its `coordinate_space: "screenshot"` to `renforge_click_at`. RenForge converts
them to logical coordinates, including when WSLg scales the capture.

## GOD-mode edit and branch check

Use real labels and visible choice text from the project in this compact
launch-to-diagnostics loop:

```text
project = "/path/to/game"
renforge_launch(project_path=project)
edit game/script.rpy
renforge_control(project_path=project, action="reload_script")
renforge_wait_until(project_path=project, label="edited_label", timeout=30.0)
renforge_screenshot(project_path=project)
renforge_saves(
    project_path=project,
    action="save",
    slot="branch-a",
    extra_info="after hot reload",
)
renforge_list_choices(project_path=project)
renforge_select_choice(project_path=project, text="Branch B")
renforge_wait_until(project_path=project, label="branch_b", timeout=30.0)
renforge_get_errors(project_path=project)
```

`renforge_screenshot` returns the current frame as an image. Use
`renforge_capture_screenshot(name="idle")` when a PNG path is needed for a
later diff or translation estimate. The save call creates a named checkpoint
before selecting Branch B; use
`renforge_saves(project_path=project, action="load", slot="branch-a")` to return
to it. `renforge_wait_until` accepts exactly one of `label`, `screen`, or
`expr`, and `timeout` is capped at 120 seconds.

## Pixel-perfect placement

Positioning a sprite, CG, or overlay to the exact pixel is otherwise a blind
loop: edit the `.rpy`, relaunch, eyeball the offset, repeat. These tools let an
agent *measure* instead of guess, then converge on coordinates live before
writing them back to the script.

```text
renforge_get_displayable_bounds(project_path, tag="eileen")
  -> bounds, center, coordinate_space="logical"
renforge_position_element(project_path, tag="eileen", xpos=960, xanchor=0.5)
  -> new bounds after the nudge (tag keeps its attributes)
renforge_screenshot(project_path, grid=100, rulers=True)
  -> a frame with a labelled coordinate grid to read positions off
renforge_diff_screenshots(project_path, before_path="before.png")
  -> bounding box of every pixel that changed vs the live frame
```

Notes:

- `renforge_position_element` re-shows an already-visible tag through a
  `Transform`, so it needs the game running and the tag on screen; it keeps the
  current image attributes (`show eileen happy` stays happy). It reports the
  bounds the tag actually rendered at, not the values you requested.
- Positions follow Ren'Py's own rule: an **integer** is absolute pixels
  (`xpos=600` is 600px) and a **float** is a fraction of the screen
  (`xpos=0.5` is the centre). Send `xpos=600`, not `xpos=600.0`, for pixels.
- `renforge_get_displayable_bounds` and `renforge_position_element` work in
  Ren'Py **logical** coordinates — the same space as `xpos`/`ypos` in a script,
  so measured values drop straight into the `.rpy`.
- Screenshot overlays (`grid`, `rulers`, `crosshair_x`/`crosshair_y`) are drawn
  in the captured image's pixel space. Capture at the game's logical resolution
  (pass `width`/`height`) so the labels read as logical coordinates.
- `renforge_diff_screenshots` needs same-size frames; `threshold` (0..255)
  absorbs anti-aliasing jitter so only real movement is reported.
- `renforge_get_ui_element_bounds` measures the focus rectangle from
  `renforge_list_ui_elements` and, when the control is an `ImageButton`, the
  alpha-painted bounds of its active state. Use `painted_bounds` as the default
  region for `renforge_estimate_translation` when available; otherwise pass an
  explicit region from `focus_bounds`.

## Tool catalogue

### Discovery and static analysis

| Tool | Purpose |
| --- | --- |
| `renforge_info` | Version, dashboard status, and active project. Call this first. |
| `renforge_context` | Active dashboard and selected Ren'Py project. |
| `renforge_inspect_project` | Lightweight Ren'Py project summary. |
| `renforge_scan_project` | Scan scripts, labels, links, and metadata. Use filters and pagination for large projects. |
| `renforge_find_references` | Exact Ren'Py definitions and usages, including text interpolations. |
| `renforge_parse_lint` | Parse `renpy lint` output. |
| `renforge_inspect_image` | Inspect a local image, with optional crop and zoom. |

### Game lifecycle

| Tool | Purpose |
| --- | --- |
| `renforge_launch` | Start or reuse a game and inject the temporary bridge. `warp` accepts `file:line`. |
| `renforge_jump` | Restart a game at a label or `file:line` using Ren'Py warp. |
| `renforge_new_game` | Start a fresh process at the `start` label. |
| `renforge_stop` | Stop the running game and remove the injected bridge. |
| `renforge_game_state` | Complete state, including variables. Pass `include=["metrics", "audio"]` to add compact render/cache/window metrics and registered-channel audio state. Omitting `include` preserves the default response. |
| `renforge_game_state_compact` | Bounded state; select variables by name or prefix. |
| `renforge_advance` | Advance the current dialogue. |
| `renforge_control` | Run one action: `advance`, `rollback`, `toggle_skip`, `toggle_auto`, `toggle_afm`, `game_menu`, `hide_windows`, `quick_save`, `quick_load`, `reload_script`, `restart_interaction`, or `quit`. |
| `renforge_send_input` | Send exactly one `text`, named `key`, or logical-coordinate `scroll` operation. Text posts character-by-character events to a focused Ren'Py `Input`; `submit=true` presses Enter. Supported keys include `enter`, `esc`, arrows, `pageup`, `pagedown`, `backspace`, `delete`, `home`, `end`, `space`, `tab`, and `f1`-`f12`. Scroll uses `{"x": ..., "y": ..., "direction": "up"|"down", "amount": 1}`. |
| `renforge_saves` | Run `save`, `load`, or `list` for named slots. Save/load require `slot`; save accepts optional `extra_info`; list accepts optional `regexp` and returns `name`, `extra_info`, and `mtime` without screenshots. |
| `renforge_screenshot` | Capture a frame; width, height, crop, scale, and `grid`/`rulers`/`crosshair` overlays are optional. Passing only one of `width`/`height` keeps the game's aspect ratio. |

### Choices and user interface

| Tool | Purpose |
| --- | --- |
| `renforge_list_choices` | Visible narrative choices. |
| `renforge_select_choice` | Select a choice by text, preferably, or index. |
| `renforge_list_ui_elements` | Visible focusable controls: ID, text, role, screen, bounds, center, state, and `frame_id`. |
| `renforge_hover_element` | Move the pointer over a control by ID or text without clicking. Supports `exact`, `screen`, and `expected_frame_id`. |
| `renforge_get_ui_element_bounds` | Report `focus_bounds` and, for `ImageButton` controls, rendered `painted_bounds` for the active state. Returns `painted_bounds_available: false` with a reason when the painted content cannot be measured. |
| `renforge_click_element` | Click a control by ID or text. Supports `exact`, `screen`, and `expected_frame_id`. |
| `renforge_click_at` | Click `logical` or `screenshot` coordinates, with `expected_frame_id` and `expected_state` guards. |
| `renforge_capture_screenshot` | Persist the current frame as a named PNG under `<project>/.renforge/captures/` and return `path`, `relative_path`, SHA-256, and dimensions for later diff/translation tools. |
| `renforge_estimate_translation` | Estimate `dx`/`dy` between two saved PNGs with Pillow, returning `confidence`, `support`, and explicit unavailability when the measure is ambiguous. |
| `renforge_find_image_on_screen` | Locate a local PNG template in the current frame and return confidence, bounds, center, and frame guard. |
| `renforge_get_displayable_bounds` | Report the logical bounds and center where a shown image tag was rendered. |
| `renforge_position_element` | Reposition a shown image tag live (`xpos`, `ypos`, anchors, align, offsets, `zoom`, `rotate`) and return its new bounds. |
| `renforge_diff_screenshots` | Diff two frames (or a saved PNG against the live frame) and return the changed region's bounding box. |

### State and controlled execution

| Tool | Purpose |
| --- | --- |
| `renforge_eval` | Evaluate a Python expression in `store`. Use for diagnosis and development only. |
| `renforge_inspect_screen` | Inspect whether a screen is active and, when shown, return its layer, JSON-safe scope, and passed arguments. |
| `renforge_get_var` | Read a store variable. |
| `renforge_set_var` | Write a store variable. |
| `renforge_poll_events` | Read label, dialogue, and exception events from a cursor. |
| `renforge_get_errors` | Read recent bridge errors or bounded crash-file tails with mtimes and exit code when tracked. |
| `renforge_wait_until` | Wait for exactly one `label`, `screen`, or `expr` condition with bounded `timeout` (maximum 120 seconds) and polling `interval`. |
| `renforge_autopilot` | Explore branches and report label coverage and crashes. |

### Project, translation, builds, and Ren'Py documentation

| Tool | Purpose |
| --- | --- |
| `renforge_assets` | Find orphaned or missing image and audio assets. |
| `renforge_languages` | List languages under `game/tl/`. |
| `renforge_translation_stats` | Report translation progress and missing strings for one language. |
| `renforge_generate_translations` | Generate or update `game/tl/<language>/`. Writes to the project. |
| `renforge_export_dialogue` | Export dialogue as plain text. |
| `renforge_web_build` | Create a browser build; requires the SDK web DLC. |
| `renforge_distribute` | Build desktop distributions (`pc`, `mac`, `linux`, and more). |
| `renforge_search_docs` | Search the offline Ren'Py documentation. |
| `renforge_get_doc` | Read an offline Ren'Py documentation page. |
| `renforge_list_docs` | List available offline documentation pages. |

## Writes and safety

These tools change game or project state: `renforge_launch`, `renforge_jump`,
`renforge_new_game`, `renforge_stop`, `renforge_advance`,
`renforge_control`, `renforge_send_input`, `renforge_saves`,
`renforge_select_choice`, `renforge_click_*`, `renforge_position_element`,
`renforge_set_var`, `renforge_generate_translations`, `renforge_web_build`, and
`renforge_distribute`.

`renforge_send_input` is also stateful: supply exactly one mode per call. Text
input fails explicitly when Ren'Py cannot verify a focused `Input`, so refresh
the screen or focus the field before retrying; a successful response means the
events were queued on the game thread, not that an unrelated screen consumed
them.

`renforge_game_state` is read-only. Its optional `include` list accepts only
`metrics` and `audio`; an unknown value is rejected so a typo cannot silently
change the response. Metrics report `render_time_ms`, an FPS estimate,
`image_cache_size`, and logical/physical window sizes. Audio reports every
registered channel with its playing filename, volume, and pause state when
Ren'Py exposes them.

`renforge_inspect_screen` is read-only and reports `active=false` clearly when
the requested screen is not shown. There is intentionally no separate style
tool yet: use `renforge_eval` as the controlled style-introspection escape
hatch when the active screen's resolved style needs investigation. Treat
`renforge_eval` as arbitrary Python execution and use it only on a trusted
local project.

Recommended practices:

- prefer `renforge_game_state_compact` to the full state;
- bound `renforge_scan_project` with `file_glob`, `symbol`, `offset`, and
  `limit`;
- use a copy or branch before generating translations or distributions;
- list the UI before clicking and always pass `frame_id`;
- after a guard error, capture or list the current screen instead of replaying
  the same click.

## Troubleshooting

| Symptom | Likely cause and resolution |
| --- | --- |
| `no running game` | Call `renforge_launch` or start the dashboard with `renforge ui`. |
| No `active_project` | Provide `project_path` explicitly (every tool accepts it), run the server from the game's directory, or select a project in the dashboard. |
| `expected_frame_id guard failed` | The screen changed; call `renforge_list_ui_elements` or `renforge_find_image_on_screen` again. |
| A click lands at the wrong place under WSLg | Reuse the `coordinate_space` returned by visual search. |
| The MCP client cannot open a display | Start `renforge ui --project …`; launch requests are delegated to the display-owning process. |
| An MCP tool is missing | Update RenForge, then restart the MCP session to reload its catalogue. |

## Quick verification

After configuration, ask the agent:

> Call `renforge_info`, inspect my Ren'Py project, then list the visible game
> controls.

A successful response contains the active project, a project summary, and
controls with bounds and a `frame_id`.
