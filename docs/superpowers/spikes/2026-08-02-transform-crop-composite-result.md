# Spike result — `Transform(crop=...)` with `rotate` / `zoom` (issue #46)

**Status:** **blocked** (measured). The gate `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED` stays closed.
**SDK:** Ren'Py **8.5.3**
**Live:** `RENFORGE_CROP_LIVE=1 pytest tests/test_editor_crop_live.py` — 3 passed.
**Criteria written first:** `2026-08-02-transform-crop-composite-criteria.md`

## The invariant under test

Every adapter shipped so far relies on one assumption: a displacement applied in authored (child)
space produces an **equal** displacement in the screen space `focus_list` reports. The bridge derives
the authored value as `source + (preview_screen − runtime_baseline_screen)` and the host attests
`runtime_rect + Δ` within 1 px.

A crop only translates and clips, which is why issue #45 could unlock pure `transform_crop`. `zoom`
scales that mapping; `rotate` turns it.

## Measured

Measured twice, two different ways, with the same result.

First exploratory: the editor's own preview path, driven with the composite gate temporarily opened.
Then, for the shipped regression test, the same **+20 px authored displacement** applied through the
`_widget_properties` preview seam directly — the channel the editor previews through, but without
analyze/commit, which is what allows a *locked* target to be measured with the gate closed.

| Target | Authored Δ | Observed screen Δ | Departure |
|---|---|---|---|
| `crop_target` — pure crop, control | `[20, 0]` | `[20, 0]` | none, 1:1 holds |
| `crop_with_zoom` — `zoom=1.25` | `[20, 0]` | `[25, 0]` | `20 × 1.25`; **+25 %**, and it grows with the drag |
| `crop_with_rotate` — `rotate=15` | `[20, 0]` | `[20, 5]` | `20·sin 15° = 5.18`; **the widget moves diagonally when dragged horizontally** |

Focus rect shape, measured against reference controls carrying identical labels outside any transform:

| Target | Reference rect | Composite rect | Measured ratio | Reading |
|---|---|---|---|---|
| `crop_with_zoom` | `154×35` | `192×43` | w ×1.247, h ×1.229 | scaled by ≈1.25 on both axes |
| `crop_with_rotate` | `132×35` | `118×67` | w ×0.894, h ×1.914 | height nearly doubles: an axis-aligned bounding box of a rotated quad |

The rotated rect's **width shrinks** (×0.894) while its height nearly doubles. The height growth is
the AABB signal; the width is additionally cut by the crop window, so it is reported but not used as
evidence. Only the height ratio is asserted in the live test.

## Verdict against the written criteria

- **Pass condition 1 — focus rects describe visible geometry:** *failed for rotate.* The rect is the
  AABB of the rotated quad, so it does not identify the shape actually painted.
- **Pass condition 3 — a child-space edit lands within 1 px:** *failed for both.* Zoom is off by 5 px
  on a 20 px nudge and diverges linearly; rotate introduces 5 px of unrequested cross-axis motion.
- Conditions 2 and 4 were not reached: with 1 and 3 failing, the shape is blocked.

The existing 1 px pixel-agreement gate would reject these edits anyway. Keeping the lock means the
editor refuses up front with an exact reason instead of failing at attestation.

## Why this is not a small fix

Recovering the mapping is not the same problem for the two properties. A zoom factor is observable
from the rect (width ratio against a natural sibling), so the delta could in principle be divided by
it. A rotation is not: an axis-aligned box does not determine the angle, and the editor would need
the transformed quad rather than a rect — which is the machinery issue #43 spiked for non-focusable
selection, not something the write chain has today.

Unlocking either would mean giving the editor a transform-aware coordinate layer, which the criteria
document placed explicitly out of scope. **That is the finding, and it belongs to issue #48
(rotation), not to a crop ticket.**

## Kept locked

`transform_crop_composite` → `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED`, unchanged from issue #45.
Nothing about the pure-crop behaviour proven by #45 was altered.
