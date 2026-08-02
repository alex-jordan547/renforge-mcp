# Spike criteria — Animated element (`at Transform(...)` / ATL) editing through `_widget_properties` seam (issue #51)

**Status:** criteria locked before measurement.
**SDK:** Ren'Py **8.5.3**.
**Surface:** in-game bridge + opt-in live fixture resources (no unproven editor logic changes).

## Question

Can RenForge safely preview, write, reload, and rebind editable position (`xpos`/`ypos`) properties on displayables styled with `at Transform(...)` or ATL animations via the `_widget_properties` recreation seam without:
1. resetting or interrupting ATL animation time progress continuously during preview updates;
2. having authored position overrides immediately overwritten by ongoing ATL position animation steps;
3. losing displayable identity or failing post-reload rebinding?

## Evidence planes (must be measured independently)

| Plane | What must be measured | How it must be measured |
|---|---|---|
| **Animation Time Progress** | ATL animation time baseline / start timestamp across `_widget_properties` previews | Evaluate ATL animation `st` (show time) / transform state before and after `_widget_properties` preview updates |
| **Preview Position Stability** | Whether `_widget_properties` `xpos`/`ypos` overrides persist vs ATL frame updates | Read bounds and screen position before, during, and after preview `show_screen` calls |
| **Source Commit & Reload** | Source patching of `xpos`/`ypos` on animated elements and post-reload state | Patch authored `xpos`/`ypos` in source, reload screen, and verify bounds + rebinding |
| **Rebinding & Lock Status** | Rebinding check for animated targets | Inspect `selected_lock_reason` and candidate observation post-reload |

## Required variants in fixture

1. **ATL Position Motion Target (`anim_pos_target`)**: Button using `at pos_anim` (ATL with `ease 2.0 xpos 400 ease 2.0 xpos 200 repeat`).
2. **ATL Non-Positional Pulse Target (`anim_style_target`)**: Button using `at pulse_anim` (ATL with `ease 1.0 alpha 0.5 ease 1.0 alpha 1.0 repeat`).
3. **Stationary Transform Target (`anim_static_transform`)**: Button using `at Transform(zoom=1.0)`.

## Pass / blocked / inconclusive (frozen before implementation)

### Pass

A run is PASS only if **all** are true:
1. Preview updates via `_widget_properties` preserve `xpos`/`ypos` overrides without ATL position overwrite.
2. Animation time / state is not reset back to `t = 0` on every `_widget_properties` preview call.
3. Source patch + reload + rebinding succeeds cleanly for animated elements.
4. No lock error or identity loss occurs on post-reload rebinding.

### Blocked

BLOCKED if any one occurs:
- `_widget_properties` preview causes displayable recreation that resets ATL animation progress (`st = 0` / animation restart stutter) on every preview step (`atl_time_reset`);
- ATL position animation overwrites `_widget_properties` `xpos`/`ypos` preview overrides on subsequent frame renders (`atl_position_override_conflict`);
- Target displayable loses identity or fails post-reload rebinding (`rebinding_failure`).

### Inconclusive

- Reproducibility across consecutive runs is inconsistent (<100% agreement);
- Fixture animation timing cannot be sampled deterministically.

## Stop conditions

- If ATL animation state cannot be sampled from runtime displayable object, stop as `inconclusive`.
- If preview call returns an unexpected engine exception, stop and report `blocked`.

## Deliverable

One report dictionary from `run_editor_animated_live_scenario` with `verdict` in
`{pass, blocked, inconclusive}` and explicit evidence fields for animation time continuity, preview stability, reload, rebinding, and repeatability.
