# Spike criteria — Designed failed-gate UI across identity, clipping, and repetition locks (issue #52)

Written **before** claiming completion of issue #52. PR #25 proved the locked-state label for one expression-position case. This acceptance specification covers missing source identity, clipping ancestry, and repeated runtime instances.

**SDK under test:** Ren'Py **8.5.3**.

## What the editor needs to be true

When a user clicks or selects a locked visual-editor target:

> The failed gate must read as a designed state rather than a silent no-op or UI bug.

A locked element with a clear reason is a feature; a click that does nothing is a defect report.

## Pass conditions

All five must hold, measured live, for issue #52 to pass:

1. **Coverage across all three gate families:**
   - **Missing source identity:** e.g., `SYNTHETIC_WIDGET_ID` (widget authored without explicit `id`).
   - **Clipping ancestry:** e.g., `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED` (composite crop + rotate/zoom).
   - **Repeated runtime instance:** e.g., `MULTI_INSTANCE_UNSUPPORTED` (widget inside a `for` loop).

2. **Selection remains visible and measurable:**
   - Selecting any locked target updates `selected_rect` to the target's bounding box `[x, y, w, h]` with `w > 0` and `h > 0`.

3. **Overlay label renders exact lock code:**
   - The overlay label text includes `[<LOCK_CODE>]` (e.g. `[SYNTHETIC_WIDGET_ID]`, `[TRANSFORM_CROP_COMPOSITE_UNSUPPORTED]`, `[MULTI_INSTANCE_UNSUPPORTED]`).

4. **Drag and write actions stay disabled:**
   - Drag attempts fail with `ok=False` and report the lock code.
   - `save_enabled` remains `False`.
   - Source bytes of the fixture file remain 100% byte-identical.

5. **Live frame evidence captured:**
   - Named PNG screenshot frames are captured for each visually distinct locked state, backing acceptance on game frame rendering rather than claimed status alone.

## Blocked conditions

- Any gate family fails to render its lock code in the overlay.
- Drag or write modifies source files when targeting a locked element.
- Bounding box is cleared or hidden instead of highlighting the selected locked target.

## Inconclusive conditions

- Flaky selection or missing live frame captures.
- Inability to launch the bridge session or reach the fixture screen.
