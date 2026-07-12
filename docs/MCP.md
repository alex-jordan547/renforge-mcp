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
when possible: it returns `active_project`, the project selected in the
dashboard, so an agent does not have to guess a path.

## Recommended workflow

```text
renforge_info
  -> active_project
renforge_launch(project_path)
  -> game and bridge are available
renforge_game_state_compact(project_path)
  -> current label and bounded state
renforge_list_ui_elements(project_path)
  -> visible controls and frame_id
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
| `renforge_game_state` | Complete state, including variables. |
| `renforge_game_state_compact` | Bounded state; select variables by name or prefix. |
| `renforge_advance` | Advance the current dialogue. |
| `renforge_control` | Run engine controls such as rollback, hot reload, quicksave/quickload, skip, or auto-forward. |
| `renforge_saves` | Save, load, or list named slots. Save/load require `slot`; list returns `name`, `extra_info`, and `mtime` without screenshots. |
| `renforge_screenshot` | Capture a frame; width, height, crop, scale, and `grid`/`rulers`/`crosshair` overlays are optional. |

### Choices and user interface

| Tool | Purpose |
| --- | --- |
| `renforge_list_choices` | Visible narrative choices. |
| `renforge_select_choice` | Select a choice by text, preferably, or index. |
| `renforge_list_ui_elements` | Visible focusable controls: ID, text, role, screen, bounds, center, state, and `frame_id`. |
| `renforge_click_element` | Click a control by ID or text. Supports `exact`, `screen`, and `expected_frame_id`. |
| `renforge_click_at` | Click `logical` or `screenshot` coordinates, with `expected_frame_id` and `expected_state` guards. |
| `renforge_find_image_on_screen` | Locate a local PNG template in the current frame and return confidence, bounds, center, and frame guard. |
| `renforge_get_displayable_bounds` | Report the logical bounds and center where a shown image tag was rendered. |
| `renforge_position_element` | Reposition a shown image tag live (`xpos`, `ypos`, anchors, align, offsets, `zoom`, `rotate`) and return its new bounds. |
| `renforge_diff_screenshots` | Diff two frames (or a saved PNG against the live frame) and return the changed region's bounding box. |

### State and controlled execution

| Tool | Purpose |
| --- | --- |
| `renforge_eval` | Evaluate a Python expression in `store`. Use for diagnosis and development only. |
| `renforge_get_var` | Read a store variable. |
| `renforge_set_var` | Write a store variable. |
| `renforge_poll_events` | Read label, dialogue, and exception events from a cursor. |
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
`renforge_control`, `renforge_saves`,
`renforge_select_choice`, `renforge_click_*`, `renforge_position_element`,
`renforge_set_var`, `renforge_generate_translations`, `renforge_web_build`, and
`renforge_distribute`.

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
| No `active_project` | Select a project in the dashboard or provide `project_path` explicitly. |
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
