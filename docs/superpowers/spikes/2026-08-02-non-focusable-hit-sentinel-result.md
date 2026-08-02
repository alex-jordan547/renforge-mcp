# Spike result — Non-focusable hit via quad ∩ colour mask (issue #43)

**Date:** 2026-08-02  
**SDK:** Ren'Py **8.5.3**  
**Criteria:** `docs/superpowers/spikes/2026-08-02-non-focusable-hit-sentinel-criteria.md`  
**Driver:** `scripts/run_hit_sentinel_spike.py` / `RENFORGE_HIT_SENTINEL_LIVE=1`

## Capability verdict

```text
capability: pass
reason:    pass_with_focusable_focus_list_unproven
```

Measured on a live demo copy with opt-in spike + fixture injection. Consecutive runs produce the
same capability class (`pass`) when fixture paint is present in the screenshot.

## What was exercised

| Probe | Ground truth | AABB | QUAD | COMP (quad ∩ colour mask) |
|---|---|---|---|---|
| Axis-aligned `add` interior/exterior | pass | pass | pass | pass |
| Plain `text` glyph (scanned) | pass | pass | pass | pass |
| Decorative `frame` interior | pass | pass | pass | pass |
| Rotated `add` (25°) interior | pass | pass | pass | pass |
| Rotated AABB corner outside paint | background | **false positive** | pass | **pass** |
| Clipped child visible / clipped-away | pass | pass | pass | pass |
| Viewport child / off-scroll | pass | pass | pass | pass |
| Focusable `textbutton` centre | sparse chrome | hits box | hits box | mask-safe |

**Agreement (representative run):** AABB ≈ 0.83, QUAD ≈ 0.92, **COMP = 1.0** over 12 probes.

## Independent ground truth

- Fixture paints unique RGB colours on a dark stage.
- Full-window screenshots are classified by colour (with limited neighbour search for anti-aliased text).
- Geometry never invents hit labels: COMP only accepts a target when the **observed** pixel colour matches that target **and** the point lies in the transformed quad (and clip rect when present).

## Mechanism notes

1. **AABB alone is insufficient** — proven by `rotated_aabb_corner`: AABB reports `hit_rotated` while GT and COMP report background.  
2. **QUAD alone** is nearly sufficient for solids; COMP adds observed paint so sparse glyphs and empty chrome do not false-select.  
3. **Sentinel mask** in this spike is the widget's own unique paint colour sampled from the independent screenshot (fixture design). A production path would inject a sentinel colour via an offscreen override; the *composition* `quad ∩ mask` is what was falsified here.  
4. **Non-focusables stay out of `focus_list`** — measured.  
5. **Focusable `focus_list` membership** after hover was not stably proven in this harness (`pass_with_focusable_focus_list_unproven`). Selection of focusables remains the existing `focus_list` path; this spike does not demote it.

## Cost (order of magnitude)

- Geometry measurement: ~few ms  
- Screenshot: ~tens of ms  
- 20 colour probes on the rotated target: ~few ms on host PIL  

No per-candidate isolation re-render was required for the colour-mask approach on this fixture.

## Limits / not claimed

- Per-pixel alpha holes remain out of scope (known COMP false-positive risk).  
- Authored/style geometry sometimes falls back to fixture coordinates when Ren'Py reports `0,0` before layout.  
- Viewport case used `yinitial` + authored child placement; arbitrary scroll gestures were not driven.  
- Production editor UI and write-back for non-focusables are **not** unlocked by this spike alone.

## How to reproduce

```bash
PYTHONPATH=src python scripts/run_hit_sentinel_spike.py \
  --output .renforge/hit-sentinel-spike/result.json

# or
RENFORGE_HIT_SENTINEL_LIVE=1 PYTHONPATH=src python -m pytest -q \
  tests/test_editor_hit_sentinel_live.py
```

## Gate decision for Stage 3

**Proceed with a selection prototype** that uses `transformed_quad ∩ observed_paint_mask` for
non-focusable candidates, gated behind an explicit Stage-3 flag. Do **not** fold this into V1
editable scope until write adapters and host selection UX are designed and proven separately.

A negative / blocked outcome was always acceptable; the measured outcome is **pass** for the hit
composition itself.
