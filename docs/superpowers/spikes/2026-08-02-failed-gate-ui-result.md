# Spike result — Designed failed-gate UI across identity, clipping, and repetition locks (issue #52)

Measured live against Ren'Py **8.5.3**.

## Executive Verdict

**Verdict:** `PASS`

All acceptance criteria set out in `2026-08-02-failed-gate-ui-criteria.md` are satisfied. The visual editor's overlay communicates failed gates as designed states across missing identity, clipping ancestry, and repeated runtime instances.

## Measured Results Summary

| Gate Family | Target | Lock Code | Selection Bounding Box | Overlay Label Rendered | Drag / Write Prevented | Source Bytes Unchanged | Captured Frame |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Missing source identity** | `textbutton "NO_ID"` | `SYNTHETIC_WIDGET_ID` | `[100, 100, 77, 44]` | `id=none x=100 y=100 [SYNTHETIC_WIDGET_ID]` | `PASS` (ok=False) | `PASS` | `failed_gate_identity.png` |
| **Clipping ancestry** | `clipped_composite_target` | `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED` | `[400, 100, 100, 50]` | `id=clipped_composite_target x=400 y=100 [TRANSFORM_CROP_COMPOSITE_UNSUPPORTED]` | `PASS` (ok=False) | `PASS` | `failed_gate_clipping.png` |
| **Repeated runtime instance** | `repeated_loop_target` | `LOOP_INSTANCE_UNSUPPORTED` | `[750, 100, 89, 44]` | `id=repeated_loop_target x=750 y=100 [LOOP_INSTANCE_UNSUPPORTED]` | `PASS` (ok=False) | `PASS` | `failed_gate_repetition.png` |
| **Unlocked control** | `unlocked_control_target` | `None` | `[100, 500, 125, 44]` | `id=unlocked_control_target x=100 y=500` | `PASS` (ok=True) | `PASS` | `failed_gate_unlocked.png` |

## Key Findings

1. **Selection Visibility:** A locked target remains fully selectable and measurable. The bounding box accurately highlights the locked element on screen.
2. **Overlay Guidance:** The exact stable lock code is rendered in brackets `[<CODE>]` in the overlay header text, ensuring full clarity to the developer.
3. **Write Protection:** Dragging and Save operations are disabled (`save_enabled=False`), and source files maintain 100% byte-identical invariance before and after interactions.
4. **Live Verification:** Game frame screenshots back every measured state.
