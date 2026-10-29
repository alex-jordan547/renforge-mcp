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

For a persistent install:

```bash
# Full — MCP + CLI + web dashboard
pipx install "renforge[ui]"

# Slim — MCP server + CLI only (no dashboard deps)
pipx install renforge
```

Then run `renforge serve` (or `renforge ui` with the full install).
`[ui]` is optional: skip it when the stdio MCP server / CLI is all you need.

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

High-risk live tools are classified at invocation time. The default is
`RENFORGE_POLICY=off` (record only). To enforce denials, set
`RENFORGE_POLICY=enforce` in the MCP server environment and pass
`authorize=true` on destructive or open-world calls, or see
[POLICY.md](POLICY.md).

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

For VS Code (GitHub Copilot), in `.vscode/mcp.json` inside your workspace:

```json
{
  "servers": {
    "renforge": {
      "command": "uvx",
      "args": ["renforge@latest", "serve"]
    }
  }
}
```

For Zed, in `settings.json`:

```json
{
  "context_servers": {
    "renforge": {
      "source": "custom",
      "command": "uvx",
      "args": ["renforge@latest", "serve"]
    }
  }
}
```

> **Windows / GUI clients:** Desktop apps (Claude Desktop, Cursor, etc.) may not
> inherit your shell `PATH`. If `uvx` is not found, set `command` to the
> absolute path of `uvx` (for example `C:\Users\you\.local\bin\uvx.exe`).

> Don't have `uv`? Run `pipx install renforge`, then replace `uvx renforge@latest`
> with `renforge` in your config (and `pipx upgrade renforge` to update).

Every project-related tool takes `project_path`. Call `renforge_info` first
when possible: it returns `active_project` with a `project_source` explaining
how it was resolved — the dashboard selection, the `serve --project` default,
or a Ren'Py project auto-detected from the current directory. A null
`active_project` only means auto-discovery found nothing: every tool still
accepts `project_path` directly (no dashboard required), so ask the user for
the game's path and continue.

`renforge_info` / `renforge_context` also advertise a structured `live_editor`
capability: the Live Editor is **enabled by default** on `renforge_launch`,
with a short public-tool `agent_workflow` and a pointer to
[docs/LIVE_EDITOR.md](LIVE_EDITOR.md). Use only public MCP tools with the
editor — never private test-only `editor_task0_*` handlers.

## Save isolation

MCP launches do **not** read or write the user's normal Ren'Py saves,
preferences, or `$HOME/.renpy` unless the agent opts in. The ready payload from
`renforge_launch` / `renforge_launch_status` includes `session_id` and an
`isolation` object (`savedir_mode`, `home_mode`, `persistent_mode`,
`preferences_mode`, resolved paths, and `exposes_user_saves`). Copy selected
host slots into the isolated session; never bind-mount the user tree.

There is no MCP-protocol isolation flag and no Roots-based save exposure.
Isolation is tool arguments plus host enforcement (`RENFORGE_POLICY=enforce`).

All bridge launch paths, including the low-level Python launcher, use the same
isolated default. One disposable session root owns saves, multipersistent data,
HOME, XDG directories, and temporary files. A `python early` adapter installs a
single Ren'Py save location before persistent/preferences load and keeps it on
subsequent storage initialization and script reload. It excludes the original
`game/saves` and `config.extra_savedirs`; `--savedir` alone does not exclude them.
The adapter uses Ren'Py's file storage and scan lifecycle and is tested against
the supported 8.5.3 SDK. SDK upgrades must pass that real-engine contract.

Persistent data and preferences are isolated through storage selection, not
through deletion or a late reset. Their `auto` modes follow the save directory:
a fresh temporary directory starts empty, while `existing` and explicit paths
retain their state. Requesting `empty` with a reused save directory is rejected
before files are touched. The former marker-only `copy` and `fixture` persistent
modes are removed; import selected save slots explicitly instead.

This is game-state isolation for ordinary Ren'Py operations. It adds no OS
sandbox, namespace, mount, network restriction, or arbitrary-Python containment.
Project source, bytecode, caches, and logs still use the project directory.

### Recipe 1 — default isolated session

```text
renforge_launch(project_path=project)
  -> session_id, isolation.exposes_user_saves=false
renforge_saves(project_path=project, action="list")
  -> slots in the disposable session savedir only
```

### Recipe 2 — copy one or more user saves into the session

```text
renforge_saves(project_path=project, action="list_user")
  -> host slots under ~/.renpy/<config.save_directory> and game/saves
renforge_launch(project_path=project)
renforge_saves(project_path=project, action="import", slots=["1-1", "branch-a"])
  -> copies those slot files into the isolated savedir
renforge_saves(project_path=project, action="load", slot="1-1", authorize=true)
```

`list_user` is observational and does not need a running game. `import` is
mutating, copies files (`.save` and `.save.json`), and is refused when the
session already exposes the user tree (`savedir=existing`).

### Recipe 3 — run against the user's save tree (all-or-nothing)

```text
renforge_launch(project_path=project, savedir="existing", authorize=true)
```

`savedir=existing` (and `RENFORGE_ISOLATION=existing`) is destructive: the game
can read and write host saves. When `RENFORGE_POLICY=enforce`, pass
`authorize=true`. The same isolation knobs exist on `renforge_jump` and
`renforge_new_game`.

## Recommended workflow

```text
renforge_info
  -> active_project, live_editor (enabled_by_default, launch_tool, guide)
renforge_launch(project_path)
  -> ready, or starting after at most 20 seconds
  -> Live Editor injected by default (floating RF control in the game window)
renforge_launch_status(project_path)  # while starting
  -> starting, ready, failed, or idle
renforge_screenshot(project_path)          # observe the frame (and editor chrome)
renforge_scene_tree(project_path)          # structured layout in logical coords
renforge_game_state_compact(project_path)  # once ready
  -> current label and bounded state
renforge_game_state(project_path, include=["metrics", "audio"])
  -> optional render/cache/window metrics and per-channel audio state
renforge_inspect_screen(project_path, name="say")
  -> active layer, JSON-safe scope, and passed screen arguments
renforge_list_ui_elements(project_path)
  -> visible controls and frame_id
renforge_editor(project_path, action="status")
  -> selected_widget_id, lock_reason, capabilities, frame_id,
     selected_source_position, preview_position
renforge_editor(project_path, action="select", x=..., y=..., expected_frame_id=frame_id)
  -> overlay hit path (not a game click)
renforge_editor(project_path, action="save", dry_run=True)
  -> unified source diff; no transaction, lint, publish, or reload
renforge_editor(project_path, action="save")
  -> overlay Save: reload + attest, or the lock code
renforge_hover_element(..., expected_frame_id=frame_id)
  -> move over a control without clicking
renforge_click_element(..., expected_frame_id=frame_id)
  -> safe interaction (or renforge_click_at with coordinate_space + frame_id)
renforge_stop(project_path)
  -> clean session teardown
```

Live Editor human and agent detail (editable vs locked, Save vs preview,
source safety): **[LIVE_EDITOR.md](LIVE_EDITOR.md)**.

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
to it. A successful load reply includes `restored_label` so the agent can
attribute the restored state before polling again. `renforge_wait_until`
accepts exactly one of `label`, `screen`, or `expr`, and `timeout` is capped at
120 seconds.

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
- Translation search is bounded to protect the MCP process. If a large region
  and `max_shift` exceed the work budget, tighten the region around the painted
  content or reduce `max_shift`.

## Whole-scene perception

`renforge_scene_tree` turns the rendered frame into structured data so a
non-multimodal agent can reason about layout without a screenshot. Unlike
`renforge_list_ui_elements` (focusable controls only), it walks every layer and
the active screens, reporting sprites, backgrounds, controls, and text blocks in
logical coordinates.

```text
renforge_scene_tree(project_path)
  -> nodes[] (id, type, bounds, center, zorder, screen), window,
     omitted{by_type,by_layer,by_reason}, truncated,
     limits{max_depth,max_nodes,max_text_chars}
renforge_scene_tree(project_path, include=["color","style","overflow"])
  -> per node: composited color.dominant, declared style, best-effort text overflow
renforge_scene_tree(project_path, format="wireframe")
  -> an ASCII map plus a legend mapping labels to ids/bounds/text
renforge_scene_tree(project_path, save_as="before")   # persist a snapshot
# ...edit a screen or advance the game...
renforge_scene_tree(project_path, diff_against="before")
  -> diff{added, removed, changed[moved|resized|text_changed|color_changed|z_changed]}
renforge_measure(project_path, action="align", targets=["quick_menu.Back","quick_menu.Skip"], tolerance=1)
  -> {result:{left,right,top,bottom,center_x,center_y}, pass}
renforge_measure(project_path, action="contrast", targets=["screens/say.text#0"])
  -> {result:{ratio, aa, aaa, fg, bg}}
```

Notes:

- Coordinates are Ren'Py **logical** pixels (the `xpos`/`ypos` space), so measured
  values drop straight into a `.rpy`. A node's `bounds` may be `null` with
  `bounds_available: false` when a displayable cannot be sized.
- The default `detail="semantic"` returns meaningful leaves and always includes an
  `omitted` count of what was filtered, so widen with `detail="layout"|"raw"`,
  `types=[…]`, or `layers=[…]` only when needed.
- If `truncated` is true, `omitted.by_reason` identifies a traversal cap and
  `limits` reports the effective bound; do not treat that response as a complete
  inventory.
- `max_text_chars` bounds node text, screen names, roles, actions, tags, layers,
  and types; truncated values retain up to that many characters and end with `…`.
  IDs use a fixed 256-character cap so they remain stable across output budgets
  and can be passed unchanged to `renforge_measure`.
- When a focusable scan hits `max_nodes`, `covered` and `clickable` are `null` with
  `coverage_reason="max_nodes"` because omitted higher controls may overlap them.
- MCP replies default to `max_items=50`, `max_output_depth=6`, and
  `max_output_bytes=65536`; lower these for tighter resource bounds or raise
  them deliberately for a larger inventory.
- `renforge_measure` targets are `scene_tree` node `id`s (resolved live) or literal
  `{x,y,width,height}` bounds; `center` and `fit` also take a `within` container.
- `color` and `contrast` sample the actual composited frame while `style` reports
  declared values, so requesting both surfaces intent-vs-render mismatches.

## ImageButton idle→hover alignment

For `imagebutton` controls with distinct idle/hover art, prefer measuring the
logical shift from `painted_bounds` before relying on a screenshot diff alone.

```text
renforge_list_ui_elements(project_path)
  -> elements[], frame_id
renforge_get_ui_element_bounds(project_path, element_id=id)
  -> focus_bounds, painted_bounds (idle), state="idle"
renforge_capture_screenshot(project_path, name="idle")
  -> path under .renforge/captures/   # invalidates the previous frame_id
renforge_list_ui_elements(project_path)
  -> fresh frame_id
renforge_hover_element(project_path, element_id=id, expected_frame_id=frame_id)
  -> hovered without click
renforge_get_ui_element_bounds(project_path, element_id=id)
  -> painted_bounds (hover), state="hover"
  # dx = hover.painted_bounds.x - idle.painted_bounds.x (logical pixels)
renforge_capture_screenshot(project_path, name="hover")
renforge_estimate_translation(before_path=idle.path, after_path=hover.path, ...)
  # best on named PNG captures; ambiguous on heavily scaled live frames
```

Re-list UI elements after every capture or screenshot when using `expected_frame_id`
guards: each capture hashes a new frame.

## Tool catalogue

### Discovery and static analysis

| Tool | Purpose |
| --- | --- |
| `renforge_info` | Version, dashboard status, active project, and the structured `live_editor` capability (enabled by default, launch tool, agent workflow, guide). Call this first. |
| `renforge_context` | Same discovery payload as `renforge_info` (active project + `live_editor`). |
| `renforge_inspect_project` | Lightweight Ren'Py project summary. |
| `renforge_scan_project` | Scan scripts, labels, links, and metadata. Use filters and pagination for large projects. |
| `renforge_find_references` | Exact Ren'Py definitions and usages, including text interpolations. |
| `renforge_parse_lint` | Parse `renpy lint` output. |
| `renforge_inspect_image` | Inspect a local image, with optional crop and zoom. |

### Game lifecycle

| Tool | Purpose |
| --- | --- |
| `renforge_launch` | Start or reuse a game with the **Live Editor enabled by default** and inject the temporary bridge. After launch, poll `renforge_launch_status`, observe with `renforge_screenshot` / `renforge_scene_tree`, then use guarded clicks — see [LIVE_EDITOR.md](LIVE_EDITOR.md). The call waits at most 20 seconds, then returns `status=starting` while startup continues in the background. A competing launch returns `code=LAUNCH_IN_PROGRESS` instead of discarding its parameters. `warp` accepts `file:line`; `display`/`audio` default to `auto`; **saves, preferences, and HOME default to isolated temporary locations** so the session does not read or write the user's normal Ren'Py state (`~/.renpy` / `game/saves` / host preferences). Ready payloads include `session_id` and an `isolation` object. Pass `savedir=existing` (host HOME follows unless `home` is set) or set `RENFORGE_ISOLATION=existing` to use the user's files; that requires `authorize=true` when `RENFORGE_POLICY=enforce`. Prefer `renforge_saves(action="import")` to copy selected host slots. `timeout` controls the background startup deadline. |
| `renforge_launch_status` | Report `starting`, `ready`, `failed`, or `idle` for the latest launch. While cancellation is pending it keeps `status=starting` and sets `cancel_requested=true`. Ready payloads repeat `session_id` and `isolation`. |
| `renforge_jump` | Restart at a label or `file:line`; it uses the same non-blocking launch lifecycle and the same isolation knobs as `renforge_launch`. |
| `renforge_new_game` | Start a fresh process at the `start` label through the same non-blocking lifecycle and isolation knobs as `renforge_launch`. |
| `renforge_stop` | Stop a running game or cancel an in-progress launch. Uses authenticated bridge quit when a session is owned; may return `SHUTDOWN_INCOMPLETE` with deferred cleanup while the process/artifacts finish tearing down. When another launch holds the project lock, reports `replacement_running`. An uninterruptible startup phase may return `launch_cancel_requested=true`; poll launch status until it becomes `failed` or `closed`. |
| `renforge_game_state` | Complete state, including variables. Pass `include=["metrics", "audio"]` to add compact render/cache/window metrics and registered-channel audio state. Omitting `include` preserves the default response. Optional `state_profile` filters the store. |
| `renforge_game_state_compact` | Bounded state (`state_profile=interaction` by default); select variables by name or prefix; supports serialization limits. |
| `renforge_advance` | Advance the current dialogue. |
| `renforge_control` | Run one action: `advance`, `rollback`, `toggle_skip`, `toggle_auto`, `toggle_afm`, `game_menu`, `hide_windows`, `quick_save`, `quick_load`, `reload_script`, `restart_interaction`, or `quit`. Emits correlated business events; `wait_for_effect=true` waits for the matching event. |
| `renforge_send_input` | Send exactly one `text`, named `key`, or logical-coordinate `scroll` operation. Text posts character-by-character events to a focused Ren'Py `Input`; `submit=true` presses Enter. Supported keys include `enter`, `esc`, arrows, `pageup`, `pagedown`, `backspace`, `delete`, `home`, `end`, `space`, `tab`, and `f1`-`f12`. Scroll uses `{"x": ..., "y": ..., "direction": "up"|"down", "amount": 1}`. |
| `renforge_saves` | Run `save`, `load`, `list`, `list_user`, or `import` for named slots. `list` reads the running session; `list_user` observes host slots without a running game; `import` copies selected host slots (`slot`, `slots`, or `regexp`) into the isolated session and refuses when the session exposes the user tree. Save/load require `slot`; save accepts optional `extra_info`; load returns `restored_label`; list/list_user accept optional `regexp` and return `name`, `extra_info`, and `mtime` without screenshots. |
| `renforge_screenshot` | Capture a frame; width, height, crop, scale, and `grid`/`rulers`/`crosshair` overlays are optional. Passing only one of `width`/`height` keeps the game's aspect ratio. |

### Choices and user interface

| Tool | Purpose |
| --- | --- |
| `renforge_list_choices` | Visible narrative choices. |
| `renforge_select_choice` | Select a choice by text, preferably, or index. |
| `renforge_list_ui_elements` | Visible focusable controls: semantic `id`, text, role, screen, `action`, bounds, center, `zorder`, `covered`, `clickable`, and `frame_id`. Coordinates are logical Ren'Py space. |
| `renforge_hover_element` | Move the pointer over a control by ID or text without clicking. Supports `exact`, `screen`, and `expected_frame_id`. |
| `renforge_get_ui_element_bounds` | Report `focus_bounds` and, for `ImageButton` controls, rendered `painted_bounds` for the active state. Returns `painted_bounds_available: false` with a reason when the painted content cannot be measured. |
| `renforge_click_element` | Click a control by ID or text. Supports `exact`, `screen`, `expected_frame_id`, `interaction_id`, and `wait_for_effect`. Returns `received_by` when another control owns the hit point. |
| `renforge_editor` | Public Live Editor: `status`, `select` (overlay hit path), and `save` (reload + attest). `save` with `dry_run` returns a unified source diff without writing, linting, reloading, or creating a transaction; it rejects `x`/`y` and older bridges without dry-run support (`DRY_RUN_UNSUPPORTED`). Locked targets return the lock code. Never call private `editor_task0_*` handlers. |
| `renforge_click_at` | Click `logical` or `screenshot` coordinates, with `expected_frame_id` and `expected_state` guards. |
| `renforge_hit_test` | Inspect the interactive focus stack at a point (`topmost` + `underneath`) to diagnose overlays that intercept clicks. |
| `renforge_capture_screenshot` | Persist the current frame as a named PNG under `<project>/.renforge/captures/` and return `path`, `relative_path`, SHA-256, and dimensions for later diff/translation tools. |
| `renforge_estimate_translation` | Estimate `dx`/`dy` between two saved PNGs with Pillow, returning `confidence`, `support`, and explicit unavailability when the measure is ambiguous. Search work is bounded; use a tighter region or lower `max_shift` when needed. |
| `renforge_find_image_on_screen` | Locate a local PNG template in the current frame and return confidence, bounds, center, and frame guard. |
| `renforge_get_displayable_bounds` | Report the logical bounds and center where a shown image tag was rendered. |
| `renforge_position_element` | Reposition a shown image tag live (`xpos`, `ypos`, anchors, align, offsets, `zoom`, `rotate`) and return its new bounds. |
| `renforge_diff_screenshots` | Diff two frames (or a saved PNG against the live frame) and return the changed region's bounding box. |
| `renforge_scene_tree` | Perceive the whole scene as logical-coordinate nodes — every layer displayable, focusable control, and text block — with `id`, `type`, `bounds`, `center`, `zorder`, `screen`, and an `omitted` completeness hint. Unlike `renforge_list_ui_elements` (focusables only) it also reports non-focusable text and images. `detail` is `semantic` (default), `layout`, or `raw`; `layers`/`types`/`screen`/`ids` scope it; `include=["color","style","overflow"]` adds composited colour, declared style, and best-effort text overflow; `format="wireframe"` adds an ASCII map; `save_as`/`diff_against` persist and diff snapshots under `<project>/.renforge/scenes/`. |
| `renforge_measure` | Measure pixel relationships between scene nodes (by `id`) or literal bounds without vision: `align`, `gap`, `distribute`, `center`, `overlap`, `fit`, and WCAG `contrast`. Returns logical-pixel deltas and, with `tolerance`, a `pass` verdict. |

### State and controlled execution

| Tool | Purpose |
| --- | --- |
| `renforge_eval` | Evaluate a Python expression in `store`. Use for diagnosis and development only. Open-world: requires `authorize=true` when `RENFORGE_POLICY=enforce`. |
| `renforge_inspect_screen` | Inspect whether a screen is active and, when shown, return its layer, JSON-safe scope, and passed arguments. |
| `renforge_get_var` | Read a store variable. |
| `renforge_set_var` | Write a store variable. |
| `renforge_poll_events` | Read label, dialogue, exception, and business events (`quick_save.completed`, `skip.stopped`, …) from a cursor. Events may carry `correlation_id` and `timestamp`. |
| `renforge_get_errors` | Read recent bridge errors or bounded crash-file tails with mtimes and exit code when tracked. |
| `renforge_wait_until` | Wait for exactly one `label`, `screen`, or `expr` condition with bounded `timeout` (maximum 120 seconds) and polling `interval`. Returns compact state by default (`state_profile=interaction`); pass `include` for extra fields and `state_profile=full` only when needed. |
| `renforge_run_scenario` | Execute a multi-step live scenario (`set`, `click`, `wait`, `assert`, …) in one call; captures diagnostics on failure. |
| `renforge_autopilot` | Explore choices backed by active Ren'Py menu `items` across fresh replays, including custom-named and one-item menus; report label coverage and crashes. Custom `call_screen` interactions that do not expose Ren'Py menu `items` are not currently explored automatically. |

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
`renforge_select_choice`, `renforge_click_*`, `renforge_run_scenario`,
`renforge_position_element`, `renforge_set_var`,
`renforge_generate_translations`, `renforge_web_build`, and
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
local project. When `RENFORGE_POLICY=enforce`, `renforge_eval`, destructive
`renforge_control` / `renforge_saves` actions, launches that expose the user
save tree (`savedir=existing` or a host save path), and matching
`renforge_run_scenario` steps are denied unless `authorize=true` or an
allowlist permits them. See [POLICY.md](POLICY.md) for the operation-level
model, its relationship to MCP `ToolAnnotations`, and the compatibility plan.

`renforge_scene_tree` is read-mostly: it never changes game state, but `save_as`
writes a JSON snapshot under `<project>/.renforge/scenes/` for later diffing.
`renforge_measure` is read-only. Requesting `include=["style"]` on
`renforge_scene_tree` is the structured alternative to the `renforge_eval` style
escape hatch above.

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
| `no running game` | If launch status is `starting`, wait and call `renforge_launch_status` again. Otherwise call `renforge_launch` or start the dashboard with `renforge ui`. |
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

## Scripted / non-interactive use

For scripts and CI, call tools through the FastMCP CLI instead of an
interactive MCP client. `--command` starts the stdio server, `--target` names
the tool, `--input-json` passes the arguments as a JSON object, and `--json`
prints FastMCP's result envelope.

The primary install is `uvx renforge@latest serve`, so use the same launcher as
your client config in place of `renforge serve`. If `renforge` is not on
`PATH`, substitute the matching launcher. The `fastmcp` CLI ships with the
`fastmcp` package, a RenForge dependency; install it if the command is not on
your `PATH`.

```bash
fastmcp call --command "renforge serve" --target renforge_info --json
```

`--json` prints FastMCP's envelope, not just the tool payload: `content` (the
result blocks), `is_error`, and `structured_content`. Read the tool payload from
`structured_content`, or from the first text block when it is absent; the
command exits 1 when `is_error` is true.

```bash
fastmcp call --command "renforge serve" --target renforge_inspect_project \
  --input-json '{"project_path": "/path/to/game"}' --json
```

Plain `key=value` tokens are not usable with `--command`: they bind to the
server-spec positional and the call is rejected, so pass arguments with
`--input-json`.

`renforge serve --project /path/to/game` only seeds the `active_project` that
`renforge_info` reports (a dashboard selection wins). It does not supply a
default project for tool calls: most project tools require `project_path`, so
pass it explicitly on every project-scoped call, as in the second example.

Under `RENFORGE_POLICY=enforce`, destructive and open-world calls are denied
unless the arguments include `"authorize": true` in `--input-json`, for example
`--input-json '{"project_path": "/path/to/game", "authorize": true}'`. See
[POLICY.md](POLICY.md). With `RENFORGE_POLICY=off` (the default) the flag is
unnecessary.

A long-lived MCP stdio client is the other documented route. Each
`fastmcp call` starts a fresh server process, so in-process state — including
launch ownership — is not shared between calls; use one stdio session for
multi-step launches that must stay under the same server's control. The
dashboard's `/api/*` endpoints are a UI surface, not a documented scripting API.

`renforge.server.create_app()` is used by the test suite to build the server
in-process, but it is not a declared stable public API and no module-level app
instance is provided; scripts should use the stdio/CLI route.

> **Version note:** These flags were checked against FastMCP 4.0.5.
> `pyproject.toml` only requires `fastmcp>=2.0.0`, so CLI flags may differ on
> other releases; check `fastmcp call --help`.
