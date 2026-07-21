# Changelog

All notable RenForge releases are recorded here. Versions follow semantic
versioning.

## [0.6.5] - 2026-07-21

### Fixed

- Long Windows startups no longer outlive the MCP request budget.
  `renforge_launch` returns `status="starting"` after at most 20 seconds while
  startup continues in the background; `renforge_launch_status` reports the
  final state, `renforge_stop` cancels an in-progress launch cleanly, and a
  competing launch returns `LAUNCH_IN_PROGRESS` instead of dropping its parameters.

## [0.6.4] - 2026-07-20

### Fixed

- `launch_with_bridge` no longer returns prematurely when `ping()` returns
  `{"error": "timeout_waiting_for_main_thread"}`. On Windows or during heavy asset
  loading / script compilation, Ren'Py can take 30–40s before `periodic_callbacks`
  starts draining on the main thread. Previously `client.ping()` returned the dict without
  raising, causing `launcher.py` to declare the session `ready` prematurely while the
  game was still in `init python`. Subsequent MCP tool calls then timed out or failed.
  `launch_with_bridge` now strictly verifies `reply.get("pong") is True` and retries
  until Ren'Py reaches its main interaction loop, with default `startup_timeout`
  increased from 60s to 90s.

## [0.6.3] - 2026-07-20

### Fixed

- Bridge listener no longer dies with `NameError: name 'socket' is not
  defined` after `renpy.reload_script()`. The listener thread survives a
  script reload, but the reload wipes the Ren'Py store — the `__globals__`
  of `init python:` functions — so the bare `except socket.timeout:` in the
  accept loop raised `NameError` on the next 0.5s timeout and silently
  killed the thread. The game kept running with a dead bridge until the
  process was eventually closed. The listener and its helpers now use
  function-local imports (read from `sys.modules`, which reload never
  touches), and the accept loop also tolerates non-timeout `OSError`.

## [Unreleased]


## [0.6.2] - 2026-07-19

### Fixed

- Ren'Py **8.5** support: read button/choice labels via `_tts_all(raw=False)`
  (8.5 made `raw` required; the old no-arg call failed open and blanked every
  control label, breaking choice selection and autopilot).
- Repair Ren'Py 8.5 `--json-dump` label emission when discovering/installing an
  SDK: unwrap Node-keyed `script.namemap` entries that upstream `dump.py` still
  filters with `isinstance(name, str)`.

### Changed

- Default pinned Ren'Py SDK is now **8.5.3** (was 8.3.7).

## [0.6.1] - 2026-07-19

### Changed

- Public-launch polish: install-first README with light/dark dashboard
  screenshots, architecture and client-config details moved to `docs/`,
  English-only dashboard labels.
- Published to the official MCP Registry (`io.github.alex-jordan547/renforge`):
  `server.json` manifest and PyPI ownership marker.

## [0.6.0] - 2026-07-16

### Added

- Compact live-state profiles (`minimal` / `interaction` / `debug` / `full`) with
  serialization limits on `renforge_wait_until` and `renforge_game_state_compact`.
- `renforge_launch` strategies: `display=auto`, `audio=auto`, structured launch
  errors (`code` / `phase` / `suggested_fix`), and `savedir=temporary` isolation.
- `renforge_hit_test` for interactive focus-stack inspection; UI elements now
  report `action`, `zorder`, `covered`, `clickable`, and logical coordinates.
- `renforge_run_scenario` to batch set/click/wait/assert steps with automatic
  failure diagnostics.
- Structured business events (`quick_save.completed`, `quick_load.completed`,
  `skip.started`/`skip.stopped`, `auto.changed`/`auto.advanced`,
  `rollback.completed`) with `correlation_id` / `interaction_id`.
- `wait_for_effect` on `renforge_control` and `renforge_click_element` to block
  until the matching business event is observed.

### Changed

- `renforge_wait_until` returns a compact interaction-profile state by default
  and a structured `matched` object (`type` + `value`).
- Clicks report the element that actually received the event (`received_by`)
  when another control covers the target.
- Bridge `poll_events` entries include `timestamp` and optional
  `correlation_id` for attribution.

### Fixed

- Translation estimation discards pixels shifted outside the search region
  instead of wrapping them around the opposite edge.
- Live save/load verification now enforces the documented `restored_label`
  response contract.

## [0.5.0] - 2026-07-13

### Added

- Added `renforge_hover_element` to move the pointer over visible controls
  without clicking, with stale-frame protection.
- Added `renforge_capture_screenshot` for reusable named PNG captures and
  `renforge_estimate_translation` for measuring visual movement between them.
- Added `renforge_get_ui_element_bounds` to report focus bounds and the
  alpha-painted bounds of the active `ImageButton` state.

### Changed

- ImageButton hover now dispatches Ren'Py's focus mouse handler before
  restarting the interaction, so hover state updates reliably without a player
  interaction loop.

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
