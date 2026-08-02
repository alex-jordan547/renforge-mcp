# Spike result — pure `Transform(crop=)` / `Crop` (issue #45)

**Status:** pass for pure crop only (measured).  
**SDK:** Ren'Py **8.5.3**  
**Live:** `RENFORGE_CROP_LIVE=1 pytest tests/test_editor_crop_live.py` — 2 passed.

## Classification

| Authored form | Live `type` | Live `crop_state` |
|---|---|---|
| `fixed at Transform(crop=(0,0,300,200))` | `Transform` | `transform_crop` |
| `fixed at Transform(crop=..., rotate=15)` | `Transform` | `transform_crop_composite` |
| `fixed at Transform(crop=..., zoom=1.25)` | `Transform` | `transform_crop_composite` |

`Crop()` is sugar for `Transform(child, crop=rect)` — no runtime `Crop` class on 8.5.3.

## Visible geometry

| Target | Measurement |
|---|---|
| `crop_target` (fully inside) | focus fully inside crop window (200,160)+(300×200) |
| `crop_partial` (ypos 185) | focus height **shorter than natural sibling** (e.g. 15 vs ~35); still fully inside crop AABB — engine clips focus |
| `crop_fullclip` (ypos 250) | **absent** from `list_ui_elements` |

## Write chain

Seven-step green for `crop_target`: resolve unlocked, preview delta match ≤1, patch xpos/ypos only,
reload committed, pixel agreement ≤1, rebind ok, byte-identical undo. Source compared via **deltas**
(screen `preview_position` ≠ child authored values under crop origin).

## Unlocked / still locked

- **Unlocked:** pure `transform_crop` only.
- **Locked:** `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED` (rotate/zoom), container, computed ypos.
