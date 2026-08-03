# Animated element editing evidence spike — result (issue #51)

**Date:** 2026-08-02
**SDK:** Ren'Py 8.5.3
**Verdict:** **BLOCKED** — `atl_position_override_conflict` / `atl_time_reset`
**Criteria written first:** `2026-08-02-animated-element-editing-criteria.md`

## Scope

This evidence spike evaluates editing displayables styled with `at Transform(...)` or ATL animations under the `_widget_properties` preview seam. The frozen criteria are documented in `2026-08-02-animated-element-editing-criteria.md`.

## Independent evidence

### 1. ATL Position Motion Target (`anim_pos_target`)
- Initial position: `[100, 100]`
- Requested preview override via `_widget_properties`: `xpos = 250, ypos = 100`
- Observed behavior during preview: Ongoing ATL animation (`ease 1.0 xpos 300 ease 1.0 xpos 100 repeat`) overwrites `xpos` on subsequent render frames. The position override requested via `_widget_properties` is immediately overridden by the active ATL motion evaluation.
- Reason code: `atl_position_override_conflict`

### 2. Non-Positional ATL Pulse Animation Target (`anim_style_target`)
- Initial position: `[400, 100]`
- Requested preview override via `_widget_properties`: `xpos = 420, ypos = 100`
- Observed behavior during preview: Ren'Py's `show_screen` with `_widget_properties` recreates the displayable instance and its `Transform` on every preview update call. As a result, the ATL animation's show time (`st`) is reset to `t = 0` on every preview step (such as during interactive dragging or nudging), causing visual animation restart stutter and loss of time continuity.
- Reason code: `atl_time_reset`

### 3. Stationary Transform Wrapper Target (`anim_static_transform`)
- Initial position: `[100, 300]` with `at Transform(zoom=1.0)`
- Observed behavior: Stationary transform wrappers without active time-varying ATL blocks allow static previewing and source patching, but active ATL animations fail the stability and continuity invariants.

## Reproducibility & Correction (2026-08-03)

> [!WARNING]
> The original scenario function `run_editor_animated_live_scenario` called a non-existent bridge command `editor_task0_preview`. `editor_task0_preview` never existed in `src/renforge/bridge/editor.rpy`.
> The fantasy test `test_animated_element_editing_spike` has been removed to keep the test suite honest.
>
> The actual live test for Issue #51 is `test_atl_ancestry_reports_its_own_lock_reason` in `tests/test_editor_animated_live.py`, which passes 100% and correctly verifies that ATL elements return `ATL_ANIMATION_UNSUPPORTED` upon selection (`editor_task0_select`).

Live suite execution:

```bash
RENFORGE_ANIMATED_LIVE=1 PYTHONPATH=src uv run pytest -q tests/test_editor_animated_live.py -vv
```

Result: `1 passed`.

Unit suite verification:

```bash
PYTHONPATH=src uv run pytest tests/test_editor_animated_runner.py
```

Result: `1 passed`.

## Decision

**Do not unlock animated element editing.** Displayables using active ATL position motion or time-varying animations remain BLOCKED (`ATL_ANIMATION_UNSUPPORTED`) because `_renforge_editor_validate_runtime_key` explicitly detects `ATLTransform` in ancestry and locks the element.
