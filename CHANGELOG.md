# Changelog

All notable RenForge releases are recorded here. Versions follow semantic
versioning; the 0.4.0 additions are backwards-compatible with 0.3.0.

## [Unreleased]

## [0.4.1] - 2026-07-13

### Added

- `renforge_info`/`renforge_context` now resolve `active_project` without the
  dashboard: they fall back to the `serve --project` default, then to a Ren'Py
  project auto-detected from the current directory, and report the winning
  source in a new `project_source` field. When nothing matches, the payload
  carries an explicit `hint` so agents pass `project_path` directly instead of
  stalling.

### Changed

- `renforge_screenshot` accepts a single `width` or `height` and derives the
  other dimension from the game's aspect ratio (previously both were required
  together).

## [0.4.0] - 2026-07-12

### Added

- Added grouped runtime controls through `renforge_control`, including hot
  script reload, rollback, quicksave/quickload, skip, auto-forward, and menu
  actions.
- Added named save slots with `renforge_saves` and grouped text, key, and scroll
  input with `renforge_send_input`.
- Added `renforge_get_errors` for recent bridge exceptions and bounded crash-file
  diagnostics, plus `renforge_wait_until` for bounded label, screen, or expression
  waits.
- Added `renforge_inspect_screen` for active screen scope and argument
  introspection.

### Changed

- Extended `renforge_game_state` with opt-in `include=["metrics", "audio"]`
  sections while preserving its default response shape.
- Expanded the README and MCP reference with the complete edit, hot-reload,
  save, branch, and diagnostics workflow.

## [0.3.0] - 2026-07-10

- Added pixel-perfect screenshot, displayable measurement, live positioning,
  and screenshot-diff tools.
