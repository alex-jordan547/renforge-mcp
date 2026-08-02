# Spike result — Non-focusable hit via quad ∩ isolated sentinel mask (issue #43)

**Date:** 2026-08-02 (revised after Codex P1 review)  
**SDK:** Ren'Py **8.5.3**  
**Criteria:** `docs/superpowers/spikes/2026-08-02-non-focusable-hit-sentinel-criteria.md`  
**Driver:** `scripts/run_hit_sentinel_spike.py` / `RENFORGE_HIT_SENTINEL_LIVE=1`

## Capability verdict

```text
capability: pass
reason:    all_pass_criteria
```

Two consecutive live runs produce the same capability class (`pass`).

## What Codex P1 required — and what this revision measures

| Requirement | Implementation |
|---|---|
| Candidate-isolated sentinel mask | Per-target isolation: siblings parked off-screen (`xpos/ypos=-4000`); screenshot; non-dark pixels = mask. **Not** full-scene colour GT. |
| Transformed quad from Ren'Py | `Transform(...).forward` matrix on `hit_rotated` (fixture uses `add Transform(Solid(...), rotate=25)`). Authored rotate metadata is **not** used for the quad. Missing seam → `blocked`. |
| Strict capability evaluation | `focusable_ok` false → blocked; GT ambiguity >10% → inconclusive; rotated AABB not falsified → blocked. No soft `pass_with_*` reasons. |
| Independent GT | Full-scene unique-colour screenshot, separate from isolation masks. |

## Probe matrix (representative pass)

| Probe | GT | AABB | QUAD | COMP (quad ∩ isolation mask) |
|---|---|---|---|---|
| Axis-aligned `add` interior/exterior | pass | pass | pass | pass |
| Plain `text` glyph | pass | pass | pass | pass |
| Decorative `frame` | pass | pass | pass | pass |
| Rotated solid interior | pass | pass | pass | pass |
| Rotated AABB corner (outside paint) | background | **false +** | may hit hull | **pass (mask rejects)** |
| Clipped visible / away | pass | pass | pass | pass |
| Viewport child / off-scroll | pass | pass | pass | pass |
| Focusable button centre | sparse chrome | box | box | mask-safe |

**Agreement (representative):** AABB ≈ 0.83, QUAD ≈ 0.83, **COMP = 1.0** (n=12).

**Isolation pixel counts (example):** add≈23k, text≈9k, frame≈31k, rotated≈37k — all required types non-empty.

**Rotated quad seam:** `transform_forward` (runtime matrix), not authored degrees.

## Cost

- Isolation mask build (7 targets, ROI scan): ~0.4–1.0 s total  
- Geometry: few ms  
- 20 mask probes on rotated target: few ms  

## Limits / not claimed

- Per-pixel alpha holes still out of scope.  
- Production selection UI and non-focusable write-back are **not** unlocked.  
- Isolation uses off-screen parking rather than a separate offscreen GL surface; the mask is still **candidate-only observed paint**.  
- Focusable membership is proven via focus system liveliness + button resolution; some harnesses only list the viewport id in `focus_list` while other focusables exist in the focus stack without public ids.

## How to reproduce

```bash
PYTHONPATH=src python scripts/run_hit_sentinel_spike.py \
  --output .renforge/hit-sentinel-spike/result.json

PYTHONPATH=src python scripts/run_hit_sentinel_spike.py --twice \
  --output .renforge/hit-sentinel-spike/result-twice.json

RENFORGE_HIT_SENTINEL_LIVE=1 PYTHONPATH=src python -m pytest -q \
  tests/test_editor_hit_sentinel_live.py
```

## Gate decision for Stage 3

**Proceed with a selection prototype** using `runtime_transformed_quad ∩ candidate_isolated_paint_mask` for non-focusable candidates, behind an explicit Stage-3 flag. Do **not** fold into V1 editable scope until product UX and write adapters are proven separately.
